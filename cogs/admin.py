import subprocess
import logging

import discord
from discord.ext import commands

from cogs import company_manager


class Admin(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.company_manager: 'company_manager.CompanyManager' = bot.get_cog('CompanyManager')
        self.user = bot.get_cog('User')

    async def cog_check(self, ctx):
        permissions = ctx.author.guild_permissions
        return permissions.administrator

    @commands.command(name='set-apply-channel', usage='{}set-apply-channel <canale>',
                      description="Imposta il canale dove verranno inviati i messaggi di approvazione Compagnie")
    async def set_apply_channel(self, ctx, channel: discord.TextChannel):
        self.bot.cfg['Channels']['company_apply_channel'] = channel.id
        self.bot.cfg.write()

        await self.bot.send_success_embed(ctx, 'set_channel', channel=channel)

    @commands.command(name='set-creation-survey-category', usage='{}set-creation-survey-category <categoria>',
                      description="Imposta la categoria dove verranno creati i canali di survey per creazione Compagnie")
    async def set_creation_survey_category(self, ctx, channel: discord.CategoryChannel):
        self.bot.cfg['CompanyCreationSurvey']['category'] = channel.id
        self.bot.cfg.write()

        await self.bot.send_success_embed(ctx, 'set_channel', channel=channel)

    @commands.command(name='set-user-channel', usage='{}set-user-channel <canale>',
                      description="Imposta il canale dove si possono eseguire i comandi utente")
    async def set_user_channel(self, ctx, channel: discord.TextChannel):
        self.bot.cfg['Channels']['user_command_channel'] = channel.id
        self.bot.cfg.write()

        await self.bot.send_success_embed(ctx, 'set_channel', channel=channel)

    @commands.command(name='set-username-check', usage='{}set-username-check <canale>',
                      description="Imposta il canale dove verrà inviata un'allerta se un utente "
                                  "con username riconosciuto dall'espressione regolare nel cfg si unisce al server")
    async def set_username_check(self, ctx, channel: discord.TextChannel):
        self.bot.cfg['Username']['channel'] = channel.id
        self.bot.cfg.write()

        await self.bot.send_success_embed(ctx, 'set_channel', channel=channel)

    @commands.command(name='companies-reload', usage="{}companies-reload", description="Riavvia il bot")
    async def companies_reload(self, ctx):
        await self.bot.send_success_embed(ctx, 'reloading')

        subprocess.call(['service', self.bot.cfg['ServiceName'], 'restart'])

    @commands.command(name='company-delete', usage="{}company-delete <name>", description="Elimina una Compagnia")
    async def company_delete(self, ctx, name: str):
        category_id, company_role_id, faction, members = self.company_manager.get_company_info(name)
        if company_role_id is None:
            await self.bot.send_error_embed(ctx, 'company_not_exists')
            return

        for member_id in members:
            member: discord.Member = ctx.guild.get_member(member_id)
            await self.user.remove_from_company(member, prevent_governatore_removal=False, delete_from_db=False)

        company_role = ctx.guild.get_role(company_role_id)
        await company_role.delete()

        category = ctx.guild.get_channel(category_id)
        for ch in category.channels:
            await ch.delete()
        await category.delete()

        self.company_manager.delete_company(name)

        await self.bot.send_success_embed(ctx, 'company_delete_success')

    @commands.command(name='company-list', usage="{}company-list", description="Stampa la lista delle Compagnie")
    async def company_list(self, ctx):
        companies = self.company_manager.get_company_list()
        embed = discord.Embed(colour=discord.Colour.gold(), title="Lista Compagnie")
        content = ''
        for name in companies:
            content += f"- {name}\n"
        embed.description = content

        await ctx.send(embed=embed)

    @commands.command(name='set-faction', usage="{}set-faction <company_name> <faction_name>",
                      description="Imposta la Fazione di appartenenza per una Compagnia")
    async def set_faction(self, ctx, company_name, faction_name):
        if faction_name not in self.bot.faction_roles:
            await self.bot.send_error_embed(ctx, 'faction_not_exists')
            return
        members = self.company_manager.get_company_members(company_name)
        faction = self.company_manager.get_faction_for(company_name)
        if members is None:
            await self.bot.send_error_embed(ctx, 'company_not_exists')
            return
        if faction is not None:
            await self.bot.send_error_embed(ctx, 'already_in_faction')
            return

        staff_role = self.bot.faction_roles[faction_name]['staff']
        member_role = self.bot.faction_roles[faction_name]['member']
        for member_id in members:
            member = ctx.guild.get_member(member_id)
            if self.bot.roles['governatore'] in member.roles or self.bot.roles['console'] in member.roles:
                await member.add_roles(staff_role)
            else:
                await member.add_roles(member_role)

        category_id = self.company_manager.get_company_category(company_name)
        emoji = self.bot.cfg['Factions'][faction_name]['emoji']
        category = ctx.guild.get_channel(category_id)
        if category is not None:
            new_name = f"{emoji} - {company_name}"
            await category.edit(name=new_name)

        self.company_manager.set_faction(company_name, faction_name)

        await self.bot.send_success_embed(ctx, 'set_faction_success')

    @commands.command(name='kick-from-faction', usage="{}kick-from-faction <company_name>",
                      description="Espelli la Compagnia dalla Fazione di appartenenza")
    async def kick_from_faction(self, ctx, company_name):
        faction_name, members = self.company_manager.get_faction_info(company_name)
        if members is None:
            await self.bot.send_error_embed(ctx, 'company_not_exists')
            return
        if faction_name is None:
            await self.bot.send_error_embed(ctx, 'not_in_faction')
            return

        staff_role = self.bot.faction_roles[faction_name]['staff']
        member_role = self.bot.faction_roles[faction_name]['member']
        for member_id in members:
            member = ctx.guild.get_member(member_id)
            await member.remove_roles(staff_role, member_role)

        category_id = self.company_manager.get_company_category(company_name)
        category = ctx.guild.get_channel(category_id)
        if category is not None:
            await category.edit(name=company_name)

        self.company_manager.kick_from_faction(company_name)

        await self.bot.send_success_embed(ctx, 'kick_from_faction_success')

    @commands.command(name='force-recruit', usage="{}force-recruit <company_name> <member>",
                      description="Rendi l'utente membro della Compagnia")
    async def force_recruit(self, ctx, company_name, member: discord.Member):
        if not self.company_manager.check_company_existence(company_name):
            await self.bot.send_error_embed(ctx, 'company_not_exists')
            return
        member_company = self.company_manager.get_company_for(member)
        if member_company is not None:
            await self.bot.send_error_embed(ctx, 'member_already_in_company')
            return

        await self.user.add_to_company(member, company_name)

        await self.bot.send_success_embed(ctx, 'force_recruit_success')

    @commands.command(name='force-kick', usage="{}force-kick <member>",
                      description="Espelli l'utente della Compagnia di appartenenza")
    async def force_kick(self, ctx, member: discord.Member):
        member_company = self.company_manager.get_company_for(member)
        if member_company is None:
            await self.bot.send_error_embed(ctx, 'member_not_in_company')
            return

        await self.user.remove_from_company(member, prevent_governatore_removal=False)

        try:
            await member.send(self.bot.get_message('expel_notify', company=member_company))
        except discord.Forbidden:
            pass
        await self.bot.send_success_embed(ctx, 'expel_success')

    @commands.command(name='set-governatore', usage="{}set-governatore <company_name> <member>",
                      description="Imposta l'utente come Governatore della Compagnia specificata")
    async def set_governatore(self, ctx, company_name, member: discord.Member):
        if self.bot.roles['governatore'] in member.roles:
            await self.bot.send_error_embed(ctx, 'already_governatore')
            return
        if not self.company_manager.check_company_existence(company_name):
            await self.bot.send_error_embed(ctx, 'company_not_exists')
            return
        member_company = self.company_manager.get_company_for(member)
        if member_company is None:
            await self.user.add_to_company(member, company_name)
        elif member_company.lower() != company_name.lower():
            await self.bot.send_error_embed(ctx, 'promote_mismatch')
            return

        if self.bot.roles['console'] in member.roles:
            await member.remove_roles(self.bot.roles['console'])
        await member.add_roles(self.bot.roles['governatore'])
        faction = self.company_manager.get_faction_for(company_name)
        if faction is not None:
            await member.remove_roles(self.bot.faction_roles[faction]['member'])
            await member.add_roles(self.bot.faction_roles[faction]['staff'])

        await self.bot.send_success_embed(ctx, 'add_governatore_success')

    @commands.command(name='set-console', usage="{}set-console <company_name> <member>",
                      description="Imposta l'utente come Console della Compagnia specificata")
    async def set_console(self, ctx, company_name, member: discord.Member):
        if self.bot.roles['console'] in member.roles:
            await self.bot.send_error_embed(ctx, 'already_console')
            return
        if not self.company_manager.check_company_existence(company_name):
            await self.bot.send_error_embed(ctx, 'company_not_exists')
            return
        member_company = self.company_manager.get_company_for(member)
        if member_company is None:
            await self.user.add_to_company(member, company_name)
        elif member_company.lower() != company_name.lower():
            await self.bot.send_error_embed(ctx, 'promote_mismatch')
            return

        if self.bot.roles['governatore'] in member.roles:
            await member.remove_roles(self.bot.roles['governatore'])
        await member.add_roles(self.bot.roles['console'])
        faction = self.company_manager.get_faction_for(company_name)
        if faction is not None:
            await member.remove_roles(self.bot.faction_roles[faction]['member'])
            await member.add_roles(self.bot.faction_roles[faction]['staff'])

        await self.bot.send_success_embed(ctx, 'add_console_success')

    @commands.command(name='companies-notify', usage="{}companies-notify <title> <message>",
                      description="Manda un Embed in un canale testuale (specificato nel config) di tutte le Compagnie")
    async def companies_notify(self, ctx, title, *, message):
        ch_name = self.bot.cfg['CompaniesNotify']['channel_name'].lower().replace(" ", "-")
        embed = discord.Embed(colour=discord.Colour.teal(), title=title, description=message)
        categories = self.company_manager.get_companies_categories()
        for company, category_id in categories.items():
            category: discord.CategoryChannel = ctx.guild.get_channel(category_id)
            for ch in category.text_channels:
                if ch.name.startswith(ch_name):
                    await ch.send(embed=embed)

        await self.bot.send_success_embed(ctx, 'companies_notify_success')

    @commands.command(name='member-list', usage="{}member-list <company_name>",
                      description="Mostra la lista dei membri della Compagnia specificata")
    async def list_members(self, ctx, company_name):
        if not self.company_manager.check_company_existence(company_name):
            await self.bot.send_error_embed(ctx, 'company_not_exists')
            return

        embed = self.user.get_member_list_embed(company_name, ctx.guild)

        await ctx.send(embed=embed)
