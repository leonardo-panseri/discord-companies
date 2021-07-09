import re
from datetime import datetime
import logging

import discord
from discord.ext import commands
from discord.ext.commands import BadArgument

from cogs import company_manager


class User(commands.Cog):
    class MemberMentioned(commands.Converter):
        async def convert(self, ctx, argument):
            if argument.startswith('<@!') and argument.endswith('>') and len(ctx.message.mentions) == 1:
                return ctx.message.mentions[0]
            else:
                raise BadArgument('Member "{}" not found'.format(argument))

    def __init__(self, bot):
        self.bot = bot
        self.company_manager: 'company_manager.CompanyManager' = bot.get_cog('CompanyManager')
        self.survey_status = dict()
        self.survey_questions = self.bot.cfg['CompanyCreationSurvey']['questions']

    async def cog_check(self, ctx: commands.Context) -> bool:
        if ctx.author.guild_permissions.administrator:
            return True

        is_cmd_channel = False
        cmd_channel = self.bot.cfg['Channels']['user_command_channel']
        if cmd_channel != '':
            is_cmd_channel = cmd_channel == ctx.channel.id
        return is_cmd_channel

    #
    # Event listeners
    #

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        name_regex = self.bot.cfg['Username']['regex']
        channel = member.guild.get_channel(self.bot.cfg['Username']['channel'])
        if channel is None:
            logging.warning("Can't find username warn channel")
            return
        if name_regex != '':
            match = re.search(name_regex, member.display_name)
            if match is not None:
                await channel.send(self.bot.get_message('username_warn', member=member.mention))

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        self.company_manager.delete_member(member.id)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if not message.author.bot and isinstance(message.channel, discord.TextChannel) and \
                message.channel.category is not None:
            if message.channel.category.id == self.bot.cfg['CompanyCreationSurvey']['category']:
                if message.channel.id in self.survey_status and self.survey_status[message.channel.id] >= 0:
                    if self.bot.roles['approve_companies'] in message.author.roles:
                        return

                    if self.survey_status[message.channel.id] < len(self.survey_questions):
                        await self.bot.send_survey_embed(message.channel,
                                                         self.survey_questions[self.survey_status[message.channel.id]])
                        self.survey_status[message.channel.id] += 1
                    else:
                        self.survey_status.pop(message.channel.id)
                        name, tag = self.company_manager.get_request_for(message.author)
                        if name is not None:
                            await self.bot.send_survey_embed(message.channel,
                                                             self.bot.cfg['CompanyCreationSurvey']['last_message'])
                            apply_embed = discord.Embed(color=discord.Color.gold())
                            apply_embed.set_author(
                                name=f'{message.author.display_name}#{message.author.discriminator}',
                                icon_url=message.author.avatar_url)
                            apply_embed.title = 'Richiesta approvazione Compagnia'
                            apply_embed.add_field(name='Nome Compagnia', value=name)
                            apply_embed.add_field(name='Tag Compagnia', value=tag)
                            apply_embed.timestamp = datetime.utcnow()
                            approval_message = await message.channel.send(embed=apply_embed)
                            self.company_manager.set_request_approval_id(message.author, approval_message)
                            await approval_message.add_reaction(self.bot.cfg['Emoji']['check'])
                            await approval_message.add_reaction(self.bot.cfg['Emoji']['cross'])

                            await message.channel.guild.get_channel(self.bot.cfg['Channels']['company_apply_channel']) \
                                .send(self.bot.get_message('company_apply_done', channel=message.channel.mention))

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        if payload.guild_id is not None:
            return
        user: discord.User = self.bot.get_user(payload.user_id)
        if user is None or user.bot:
            return
        emoji_check = self.bot.cfg['Emoji']['check']
        emoji_cross = self.bot.cfg['Emoji']['cross']
        emoji = str(payload.emoji)
        if emoji not in [emoji_check, emoji_cross]:
            return
        dm_channel = user.dm_channel
        if dm_channel is None:
            dm_channel = await user.create_dm()
        message: discord.Message = await dm_channel.fetch_message(payload.message_id)
        if not message.author.bot:
            return
        if len(message.embeds) != 1 or len(message.embeds[0].fields) != 1:
            return
        company_name = None
        try:
            company_name = message.embeds[0].fields[0].__getattribute__('value')
        except AttributeError:
            return

        await message.remove_reaction(emoji_check, self.bot.user)
        await message.remove_reaction(emoji_cross, self.bot.user)
        if emoji == emoji_check:
            member = self.bot.guilds[0].get_member(user.id)
            if member is None:
                await self.bot.send_error_embed(user, 'no_longer_in_server')
                await message.delete()
                return
            if self.company_manager.get_company_for(member) is not None:
                await self.bot.send_error_embed(user, 'already_in_company')
                await message.delete()
                return

            try:
                await self.add_to_company(member, company_name)
            except CompanyError:
                await self.bot.send_error_embed(user, 'company_not_exists')
                await message.delete()
                return
            except RoleError:
                await self.bot.send_error_embed(user, 'role_error')
                await message.delete()
                return

            success_embed = discord.Embed(colour=discord.Colour.green(), title="Invito Accettato",
                                          description=self.bot.get_message('join_company_success'))
            await message.edit(embed=success_embed)
        elif emoji == emoji_cross:
            abort_embed = discord.Embed(colour=discord.Colour.red(), title="Invito Rifiutato",
                                        description=self.bot.get_message('join_company_abort'))
            await message.edit(embed=abort_embed)

    #
    # Commands
    #

    @commands.cooldown(1, 5 * 60, commands.BucketType.user)
    @commands.command(name='crea-compagnia', usage='{}crea-compagnia <nome> <tag>', description="Crea una Compagnia")
    async def create_company(self, ctx: commands.Context, name: str, tag: str):
        if ctx.author.guild_permissions.administrator:
            ctx.command.reset_cooldown(ctx)
        apply_channel: discord.TextChannel = ctx.guild.get_channel(self.bot.cfg['Channels']['company_apply_channel'])
        survey_category: discord.CategoryChannel = ctx.guild.get_channel(
            self.bot.cfg['CompanyCreationSurvey']['category'])
        if apply_channel is None or survey_category is None or not isinstance(survey_category, discord.CategoryChannel):
            ctx.command.reset_cooldown(ctx)
            return await self.bot.send_error_embed(ctx, 'not_configured')
        if len(tag) > 4:
            ctx.command.reset_cooldown(ctx)
            return await self.bot.send_error_embed(ctx, 'tag_invalid')
        if self.company_manager.get_company_for(ctx.author) is not None:
            ctx.command.reset_cooldown(ctx)
            return await self.bot.send_error_embed(ctx, 'already_in_company')
        if self.company_manager.check_request_existance_for(ctx.author):
            ctx.command.reset_cooldown(ctx)
            return await self.bot.send_error_embed(ctx, 'request_pending')
        if self.company_manager.check_company_existence(name, include_requests=True):
            ctx.command.reset_cooldown(ctx)
            return await self.bot.send_error_embed(ctx, 'company_already_exists')
        if self.company_manager.check_tag_existence(tag):
            ctx.command.reset_cooldown(ctx)
            return await self.bot.send_error_embed(ctx, 'tag_already_exists')

        overwrites = {
            ctx.guild.default_role: discord.PermissionOverwrite(read_messages=False, add_reactions=False),
            ctx.author: discord.PermissionOverwrite(read_messages=True)
        }
        survey_channel: discord.TextChannel = await survey_category \
            .create_text_channel(name=f"Richiesta di {ctx.author.display_name}",
                                 overwrites=overwrites)
        await survey_channel.send(ctx.author.mention)
        msg = await self.bot.send_survey_embed(survey_channel, self.bot.cfg['CompanyCreationSurvey']['first_message'])
        await msg.add_reaction(self.bot.cfg['Emoji']['cross'])
        await self.bot.send_survey_embed(survey_channel, self.survey_questions[0])
        self.survey_status[survey_channel.id] = 1

        self.company_manager.create_company_request(ctx.author, name, tag, survey_channel)

        await self.bot.send_success_embed(ctx, 'company_apply_success', channel=survey_channel.mention)

    @commands.command(name='lista-membri', usage="{}lista-membri", description="Mostra la lista dei membri della tua Compagnia")
    async def list_members(self, ctx):
        company = self.company_manager.get_company_for(ctx.author)
        if company is None:
            await self.bot.send_error_embed(ctx, 'not_in_company')
            return
        if not (self.bot.roles['console'] in ctx.author.roles or self.bot.roles['governatore'] in ctx.author.roles):
            await self.bot.send_error_embed(ctx, 'only_company_staff')
            return

        embed = self.get_member_list_embed(company, ctx.guild)

        await ctx.send(embed=embed)

    @commands.command(name='recluta', usage="{}recluta <utente>", description="Invita un utente ad unirsi alla tua Compagnia")
    async def recruit(self, ctx, member: discord.Member):
        company = self.company_manager.get_company_for(ctx.author)
        member_company = self.company_manager.get_company_for(member)
        if company is None:
            await self.bot.send_error_embed(ctx, 'not_in_company')
            return
        if not (self.bot.roles['console'] in ctx.author.roles or self.bot.roles['governatore'] in ctx.author.roles):
            await self.bot.send_error_embed(ctx, 'only_company_staff')
            return
        if member_company is not None:
            await self.bot.send_error_embed(ctx, 'member_already_in_company')
            return

        try:
            recruit_embed = discord.Embed(colour=discord.Colour.gold(),
                                          title=self.bot.get_message('recruit_embed_title'))
            recruit_embed.add_field(name="Compagnia", value=company)
            recruit_embed.description = self.bot.get_message('recruit_embed_content',
                                                             check=self.bot.cfg['Emoji']['check'],
                                                             cross=self.bot.cfg['Emoji']['cross'])
            message: discord.Message = await member.send(embed=recruit_embed)
            await message.add_reaction(self.bot.cfg['Emoji']['check'])
            await message.add_reaction(self.bot.cfg['Emoji']['cross'])
        except discord.Forbidden:
            await self.bot.send_error_embed(ctx, 'dm_disabled')
            return

        await self.bot.send_success_embed(ctx, 'invite_success')

    @commands.command(name='lascia-compagnia', usage="{}lascia-compagnia", description="Lascia la Compagnia di cui fai parte")
    async def leave_company(self, ctx):
        try:
            await self.remove_from_company(ctx.author)
        except CompanyError:
            await self.bot.send_error_embed(ctx, 'not_in_company')
            return
        except RoleError:
            await self.bot.send_error_embed(ctx, 'is_governatore_error')
            return

        await self.bot.send_success_embed(ctx, 'leave_company_success')

    @commands.command(name='espelli', usage="{}espelli <utente>", description="Espelli l'utente dalla tua Compagnia")
    async def kick_from_company(self, ctx, member: discord.Member):
        company = self.company_manager.get_company_for(ctx.author)
        member_company = self.company_manager.get_company_for(member)
        if company is None:
            await self.bot.send_error_embed(ctx, 'not_in_company')
            return
        if not (self.bot.roles['console'] in ctx.author.roles or self.bot.roles['governatore'] in ctx.author.roles):
            await self.bot.send_error_embed(ctx, 'only_company_staff')
            return
        if member_company != company:
            await self.bot.send_error_embed(ctx, 'member_not_in_your_company')
            return
        if self.bot.roles['console'] in member.roles and self.bot.roles['governatore'] not in ctx.author.roles:
            await self.bot.send_error_embed(ctx, 'expel_console_error')
            return

        try:
            await self.remove_from_company(member)
        except RoleError:
            await self.bot.send_error_embed(ctx, 'expel_governatore_error')
            return
        except CompanyError:
            logging.error("Unexpected CompanyError", exc_info=True)

        try:
            embed = discord.Embed(colour=discord.Colour.red(), title="Espulsione",
                                  description=self.bot.get_message('expel_notify', company=company))
            await member.send(embed=embed)
        except discord.Forbidden:
            pass
        await self.bot.send_success_embed(ctx, 'expel_success')

    @commands.command(name='comandi', usage="{}comandi", description="Mostra questo messaggio di aiuto")
    async def companies_help(self, ctx):
        help_embed = discord.Embed(color=discord.Colour.blue(), title='__**Comandi Compagnie**__')
        admin_help_embed = discord.Embed(color=discord.Colour.blue(), title='__**Comandi Amministratore Compagnie**__')





        prefix = self.bot.command_prefix
        user_cmd = ''
        for command in self.get_commands():
            user_cmd += '`%s` **-** %s\n' % (command.usage.format(prefix), command.description)
        help_embed.description = 'Ecco la lista dei comandi:\n' + user_cmd

        await ctx.send(embed=help_embed)

        if ctx.author.guild_permissions.administrator:
            admin_cmd = ''
            for command in self.bot.get_cog('Admin').get_commands():
                admin_cmd += '`%s` **-** %s\n' % (command.usage.format(prefix), command.description)
            admin_help_embed.description = 'Comandi Amministratore:\n' + admin_cmd
            await ctx.send(embed=admin_help_embed)

    #
    # Helpers
    #

    async def add_to_company(self, member: discord.Member, company: str):
        tag, role_id, faction = self.company_manager.get_company_basic_info(company)
        if role_id is None:
            raise CompanyError
        role = self.bot.guilds[0].get_role(role_id)
        if role is None:
            raise RoleError
        if faction is not None:
            faction_member_role = self.bot.faction_roles[faction]['member']
            await member.add_roles(role, self.bot.roles['to_add'], faction_member_role)
        else:
            await member.add_roles(role, self.bot.roles['to_add'])
        await member.remove_roles(self.bot.roles['to_remove'])
        if member is not member.guild.owner:
            try:
                await member.edit(nick=f"{tag} - {member.display_name}")
            except discord.Forbidden:
                logging.warning("Can't modify username of " + member.display_name)
        self.company_manager.add_member_to_company(company, member)

    async def remove_from_company(self, member: discord.Member, prevent_governatore_removal=True, delete_from_db=True):
        company = self.company_manager.get_company_for(member)
        if company is None:
            raise CompanyError
        if self.bot.roles['governatore'] in member.roles:
            if prevent_governatore_removal:
                raise RoleError
            else:
                await member.remove_roles(self.bot.roles['governatore'])

        self.company_manager.reset_donations(member)
        if member is not member.guild.owner:
            try:
                await member.edit(nick=None)
            except discord.Forbidden:
                logging.warning("Can't modify username of " + member.display_name)
        tag, role_id, faction = self.company_manager.get_company_basic_info(company)
        role = member.guild.get_role(role_id)
        console_role = self.bot.roles['console']
        if faction is not None:
            faction_member_role = self.bot.faction_roles[faction]['member']
            faction_staff_role = self.bot.faction_roles[faction]['staff']
            await member.remove_roles(role, self.bot.roles['to_add'], console_role, faction_staff_role,
                                      faction_member_role)
        else:
            await member.remove_roles(role, self.bot.roles['to_add'], console_role)
        await member.add_roles(self.bot.roles['to_remove'])
        if delete_from_db:
            self.company_manager.remove_member_from_company(member)

    def get_member_list_embed(self, company, guild):
        members = self.company_manager.get_company_members(company)
        embed = discord.Embed(colour=discord.Colour.teal(), title=f"Membri {company}")
        governatore = ''
        consoli = ''
        membri = ''
        for member_id in members:
            member = guild.get_member(member_id)

            if member is None:
                logging.warning(f"Member with id {member_id} cannot be found in current guild")
                self.company_manager.delete_member(member_id)
                break

            if self.bot.roles['governatore'] in member.roles:
                governatore += f"{member.display_name}\n"
            elif self.bot.roles['console'] in member.roles:
                consoli += f"- {member.display_name}\n"
            else:
                membri += f"- {member.display_name}\n"
        embed.add_field(name="Governatore", value=governatore)
        embed.add_field(name="Consoli", value=consoli if len(consoli) > 0 else "Nessun Console")
        embed.add_field(name="Membri", value=membri if len(membri) > 0 else "Nessun Membro", inline=False)
        return embed


class CompanyError(Exception):
    pass


class RoleError(Exception):
    pass
