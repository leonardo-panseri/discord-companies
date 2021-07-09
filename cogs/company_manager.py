import logging
from typing import Optional

import discord
from discord.ext import commands
from sqlalchemy.orm import Session

from database import Database


class CompanyManager(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.user_model: Database.User.__class__ = self.bot.db.User
        self.company_model: Database.Company.__class__ = self.bot.db.Company
        self.request_model: Database.CompanyRequest.__class__ = self.bot.db.CompanyRequest
        self.approval_messages = self.fetch_approval_messages()

    def create_session(self) -> Session:
        return self.bot.db.Session()

    #
    # Listeners
    #

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        emoji_check = self.bot.cfg['Emoji']['check']
        emoji_cross = self.bot.cfg['Emoji']['cross']

        emoji = str(payload.emoji)
        member: discord.Member = payload.member
        if member is None or member.bot:
            return
        if emoji not in [emoji_check, emoji_cross]:
            return

        channel: discord.TextChannel = self.bot.get_channel(payload.channel_id)
        if channel is not None and channel.category is not None:
            if channel.category.id == self.bot.cfg['CompanyCreationSurvey']['category']:
                if self.bot.roles['approve_companies'] not in member.roles:
                    return

                if payload.message_id in self.approval_messages:
                    requester_id, company_name, company_tag = self.get_request_by_message(payload.message_id)
                    requester: discord.Member = member.guild.get_member(requester_id)
                    if emoji == emoji_check:
                        role: discord.Role = await member.guild.create_role(name=company_name)
                        governatore_role: discord.Role = self.bot.roles['governatore']
                        await requester.add_roles(role, governatore_role, self.bot.roles['to_add'])
                        await requester.remove_roles(self.bot.roles['to_remove'])
                        if requester is not member.guild.owner:
                            try:
                                await requester.edit(nick=f"{company_tag} - {requester.display_name}")
                            except discord.Forbidden:
                                logging.warning("Can't modify username of " + requester.display_name)

                        category: discord.CategoryChannel = await member.guild.create_category(company_name)

                        staff_role = self.bot.roles['connect_to_voice']
                        see_voice = self.bot.roles['view_voice_channels']
                        see_voice_2 = self.bot.roles['view_voice_channels_2']
                        voice_ow = {
                            member.guild.default_role: discord.PermissionOverwrite(connect=False, view_channel=False),
                            staff_role: discord.PermissionOverwrite(connect=True, view_channel=True),
                            see_voice: discord.PermissionOverwrite(view_channel=True, connect=False),
                            see_voice_2: discord.PermissionOverwrite(view_channel=True, connect=False),
                            role: discord.PermissionOverwrite(connect=True, view_channel=True)
                        }

                        console_role = self.bot.roles['console']
                        text_ow = {
                            member.guild.default_role: discord.PermissionOverwrite(read_messages=False,
                                                                                   view_channel=False),
                            governatore_role: discord.PermissionOverwrite(mention_everyone=True),
                            console_role: discord.PermissionOverwrite(mention_everyone=True),
                            role: discord.PermissionOverwrite(read_messages=True, send_messages=True, view_channel=True)
                        }
                        admin_ow = {
                            member.guild.default_role: discord.PermissionOverwrite(read_messages=False,
                                                                                   view_channel=False),
                            governatore_role: discord.PermissionOverwrite(send_messages=True, mention_everyone=True),
                            console_role: discord.PermissionOverwrite(send_messages=True, mention_everyone=True),
                            role: discord.PermissionOverwrite(read_messages=True, send_messages=False)
                        }

                        ch_section = self.bot.cfg['CompanyChannels']
                        for id in ch_section:
                            ch_name: str = ch_section[id]['name'].format(tag=company_tag)
                            ch_type = ch_section[id]['type']

                            if ch_type == "text":
                                new_ch: discord.TextChannel = await category.create_text_channel(ch_name)
                                if ch_section[id]['admin'] == 'true':
                                    await new_ch.edit(overwrites=admin_ow)
                                else:
                                    await new_ch.edit(overwrites=text_ow)

                            elif ch_type == "voice":
                                await category.create_voice_channel(ch_name, overwrites=voice_ow)
                            else:
                                logging.warning(f"[CompanyChannels] [[{id}]] does not have a valid type")

                        self.create_company(company_name, company_tag, category, role, requester)

                        await self.bot.send_success_embed(requester, 'company_creation_success')
                    else:
                        await self.bot.send_error_embed(requester, 'company_creation_failure')

                    self.approval_messages.remove(payload.message_id)
                    await channel.delete()
                    self.delete_company_request(requester)
                else:
                    if emoji == emoji_cross:
                        self.delete_company_request_from_channel(channel)
                        self.bot.get_cog('User').survey_status.pop(channel.id)
                        await channel.delete()

    #
    # Methods
    #

    def get_company_for(self, user: discord.Member) -> Optional[str]:
        session = self.create_session()
        query = session.query(self.user_model).filter_by(member_id=user.id)

        session.close()

        if query.count() == 1:
            return query[0].company_name
        else:
            return None

    def check_company_existence(self, name: str, include_requests: bool = False) -> bool:
        session = self.create_session()
        query = session.query(self.company_model).filter_by(name=name)
        query2 = session.query(self.request_model).filter_by(name=name)

        session.close()

        if query.count() == 1 or (include_requests and query2.count() == 1):
            return True
        else:
            return False

    def check_tag_existence(self, tag: str) -> bool:
        session = self.create_session()
        query = session.query(self.company_model).filter_by(tag=tag)
        query2 = session.query(self.request_model).filter_by(tag=tag)

        session.close()

        if query.count() == 1 or query2.count() == 1:
            return True
        else:
            return False

    def check_request_existance_for(self, user: discord.Member):
        session = self.create_session()
        query = session.query(self.request_model).filter_by(member_id=user.id)

        session.close()

        if query.count() == 1:
            return True
        else:
            return False

    def create_company_request(self, member: discord.Member, name: str, tag: str, channel: discord.TextChannel):
        session = self.create_session()
        new_request = self.request_model(member_id=member.id, name=name, tag=tag, approve_channel_id=channel.id)
        session.add(new_request)

        session.commit()
        session.close()

    def delete_company_request(self, member: discord.Member):
        session = self.create_session()
        query = session.query(self.request_model).filter_by(member_id=member.id)

        if query.count() == 1:
            session.delete(query[0])
            session.commit()
        session.close()

    def delete_company_request_from_channel(self, channel: discord.TextChannel):
        session = self.create_session()
        query = session.query(self.request_model).filter_by(approve_channel_id=channel.id)

        if query.count() == 1:
            session.delete(query[0])
            session.commit()

        session.close()

    def get_request_by_message(self, message_id: int):
        session = self.create_session()
        query = session.query(self.request_model).filter_by(approve_message_id=message_id)

        session.close()

        if query.count() == 1:
            return query[0].member_id, query[0].name, query[0].tag
        else:
            return None, None

    def get_request_for(self, member: discord.Member):
        session = self.create_session()
        query = session.query(self.request_model).filter_by(member_id=member.id)

        session.close()

        if query.count() == 1:
            return query[0].name, query[0].tag
        else:
            return None, None

    def set_request_approval_id(self, member: discord.Member, message: discord.Message):
        session = self.create_session()
        query = session.query(self.request_model).filter_by(member_id=member.id)

        if query.count() == 1:
            query[0].approve_message_id = message.id
            self.approval_messages.append(message.id)

        session.commit()
        session.close()

    def fetch_approval_messages(self):
        session = self.create_session()
        query = session.query(self.request_model.approve_message_id)

        session.close()

        res = list()
        for row in query:
            res.append(row.approve_message_id)
        return res

    def create_company(self, name, tag, category, role: discord.Role, governor: discord.Member):
        session = self.create_session()
        new_company = self.company_model(name=name, tag=tag, category_id=category.id, role=role.id)
        session.add(new_company)

        query = session.query(self.user_model).filter_by(member_id=governor.id)
        if query.count() == 1:
            query[0].company = new_company
        else:
            new_user = self.user_model(member_id=governor.id)
            session.add(new_user)
            new_user.company = new_company

        session.commit()
        session.close()

    def get_company_members(self, name):
        session = self.create_session()
        query = session.query(self.company_model).filter_by(name=name)

        if query.count() == 1:
            members = []
            for member in query[0].members:
                members.append(member.member_id)

            session.close()

            return members
        else:
            session.close()

            return None

    def get_company_category(self, name):
        session = self.create_session()
        query = session.query(self.company_model).filter_by(name=name)

        session.close()

        if query.count() == 1:
            return query[0].category_id
        else:
            return None

    def get_company_basic_info(self, name):
        session = self.create_session()
        query = session.query(self.company_model).filter_by(name=name)

        session.close()

        if query.count() == 1:
            return query[0].tag, query[0].role, query[0].faction
        else:
            return None, None

    def get_company_info(self, name):
        session = self.create_session()
        query = session.query(self.company_model).filter_by(name=name)

        if query.count() == 1:
            members = []
            for member in query[0].members:
                members.append(member.member_id)

            session.close()

            return query[0].category_id, query[0].role, query[0].faction, members
        else:
            session.close()

            return None, None, None, None

    def delete_company(self, name):
        session = self.create_session()
        query = session.query(self.company_model).filter_by(name=name)

        if query.count() == 1:
            session.delete(query[0])
            session.commit()

        session.close()

    def get_company_list(self):
        session = self.create_session()
        query = session.query(self.company_model.name)

        session.close()

        res = list()
        for row in query:
            res.append(row.name)
        return res

    def get_companies_categories(self):
        session = self.create_session()
        query = session.query(self.company_model.name, self.company_model.category_id)

        session.close()

        res = dict()
        for row in query:
            res[row.name] = row.category_id
        return res

    def set_faction(self, company_name, faction):
        session = self.create_session()
        query = session.query(self.company_model).filter_by(name=company_name)

        if query.count() == 1:
            query[0].faction = faction
            session.commit()

        session.close()

    def get_faction_for(self, company_name):
        session = self.create_session()
        query = session.query(self.company_model).filter_by(name=company_name)

        session.close()

        if query.count() == 1:
            return query[0].faction
        else:
            return None

    def get_faction_info(self, company_name):
        session = self.create_session()
        query = session.query(self.company_model).filter_by(name=company_name)

        if query.count() == 1:
            members = []
            for member in query[0].members:
                members.append(member.member_id)

            session.close()

            return query[0].faction, members
        else:
            session.close()
            return None, None

    def kick_from_faction(self, company_name):
        session = self.create_session()
        query = session.query(self.company_model).filter_by(name=company_name)

        if query.count() == 1:
            query[0].faction = None
            session.commit()

        session.close()

    def add_member_to_company(self, company_name, member: discord.Member):
        session = self.create_session()
        company = session.query(self.company_model).filter_by(name=company_name)[0]
        query = session.query(self.user_model).filter_by(member_id=member.id)

        if query.count() == 1:
            query[0].company = company
        else:
            new_user = self.user_model(member_id=member.id)
            session.add(new_user)
            new_user.company = company

        session.commit()
        session.close()

    def remove_member_from_company(self, member: discord.Member):
        session = self.create_session()
        query = session.query(self.user_model).filter_by(member_id=member.id)

        if query.count() == 1:
            query[0].company = None
            session.commit()

        session.close()

    def reset_donations(self, member: discord.Member):
        session = self.create_session()
        query = session.query(self.user_model).filter_by(member_id=member.id)

        if query.count() == 1:
            query[0].company_donations = 0
            session.commit()

        session.close()

    def delete_member(self, member_id):
        session = self.create_session()
        query = session.query(self.user_model).filter_by(member_id=member_id)

        if query.count() == 1:
            session.delete(query[0])
            session.commit()

        session.close()
