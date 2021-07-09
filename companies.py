import logging
import logging.handlers
import sys

import discord
from configobj import ConfigObj
from discord.ext import commands
from validate import Validator

from cogs import user, admin, company_manager
from database import Database

logging.basicConfig(level=logging.INFO)
fh = logging.handlers.RotatingFileHandler('logs/error.log', maxBytes=1000000, backupCount=4)
fh.setLevel(logging.INFO)
fh.setFormatter(logging.Formatter(fmt='%(asctime)s %(levelname)s : %(name)s : %(message)s', datefmt='%m-%d %H:%M:%S'))
logging.getLogger('').addHandler(fh)


class Config:
    def __init__(self):
        self.cfgspec = ['[SpecialRoles]', '__many__ = id',
                        '[Channels]', '__many__ = id',
                        '[CompanyCreationSurvey]', 'category = id', 'questions = custom_list',
                        '[Username]', 'channel = id',
                        '[Factions]', '[[__many__]]', 'member_role = id', 'staff_role = id',
                        '[Messages]', '__many__ = multiline', ]
        self.checks = {
            'multiline': self.multiline,
            'id': self.id,
            'custom_list': self.custom_list
        }

    def load(self):
        cfg = ConfigObj('config.ini', configspec=self.cfgspec, encoding='utf8', list_values=False)
        cfg.validate(Validator(self.checks))
        return cfg

    def multiline(self, value):
        return value.replace('\\n', '\n')

    def id(self, value):
        try:
            res = int(value)
        except ValueError:
            res = '""'
        return res

    def custom_list(self, value: str):
        return value.split(',')


class CompaniesClient(commands.Bot):
    def __init__(self, **options):
        self.cfg = Config().load()

        self.db = None
        super().__init__(self.cfg['Prefix'], **options)

        self.add_check(self.globally_block_dms)

        self.roles = {
            'connect_to_voice': None,
            'approve_companies': None,
            'view_voice_channels': None,
            'view_voice_channels_2': None,
            'governatore': None,
            'console': None,
            'to_remove': None,
            'to_add': None
        }
        self.faction_roles = {}

    async def on_ready(self):
        self.fetch_roles()
        self.db = Database(self)
        self.load_cogs()

        await self.change_presence(activity=discord.Activity(type=discord.ActivityType.listening,
                                                             name=f"{self.command_prefix}comandi"))

        logging.info("Companies loaded in {0} servers".format(len(self.guilds)))

    async def on_command_error(self, ctx, exception):
        if isinstance(exception, (commands.errors.MissingRequiredArgument, commands.errors.TooManyArguments)):
            await self.send_error_embed(ctx, 'incorrect_command_usage',
                                        cmd=ctx.command.usage.format(self.cfg['Prefix']))
        elif isinstance(exception, commands.errors.BadArgument):
            await self.send_error_embed(ctx, 'bad_command_arguments')
        elif isinstance(exception, commands.errors.CheckFailure):
            if ctx.cog.qualified_name == 'Admin':
                await self.send_error_embed(ctx, 'no_permissions')
        elif isinstance(exception, commands.errors.CommandOnCooldown):
            await self.send_error_embed(ctx, 'cooldown', time=int(exception.retry_after))
        elif isinstance(exception, commands.errors.CommandNotFound):
            pass
        else:
            await super().on_command_error(ctx, exception)

    async def globally_block_dms(self, ctx):
        return ctx.guild is not None

    def fetch_roles(self):
        guild: discord.Guild = self.guilds[0]

        roles_sec: dict = self.cfg['SpecialRoles']
        self.roles['connect_to_voice'] = guild.get_role(roles_sec['connect_to_voice'])
        self.roles['approve_companies'] = guild.get_role(roles_sec['approve_companies'])
        self.roles['view_voice_channels'] = guild.get_role(roles_sec['view_voice_channels'])
        self.roles['view_voice_channels_2'] = guild.get_role(roles_sec['view_voice_channels_2'])
        self.roles['governatore'] = guild.get_role(roles_sec['governatore'])
        self.roles['console'] = guild.get_role(roles_sec['console'])
        self.roles['to_remove'] = guild.get_role(roles_sec['to_remove'])
        self.roles['to_add'] = guild.get_role(roles_sec['to_add'])

        factions_sec: dict = self.cfg['Factions']
        for faction, roles in factions_sec.items():
            staff_role = guild.get_role(roles['staff_role'])
            if staff_role is None:
                logging.error(f"Il ruolo staff della fazione {faction} non è stato configurato correttamente")
            member_role = guild.get_role(roles['member_role'])
            if member_role is None:
                logging.error(f"Il ruolo membri della fazione {faction} non è stato configurato correttamente")
            self.faction_roles[faction] = {}
            self.faction_roles[faction]['staff'] = staff_role
            self.faction_roles[faction]['member'] = member_role

        for key in self.roles.keys():
            if self.roles[key] is None:
                logging.error(f"Il ruolo {key} non è stato configurato correttamente")

    def load_cogs(self):
        self.add_cog(company_manager.CompanyManager(self))
        self.add_cog(user.User(self))
        self.add_cog(admin.Admin(self))

    def get_message(self, message: str, **kwargs):
        return self.cfg['Messages'][message].format(**kwargs)

    async def send_success_embed(self, ctx, message: str, **kwargs):
        embed = discord.Embed(
            color=discord.Colour.green(),
            description=self.get_message(message, **kwargs))
        await ctx.send(embed=embed)

    async def send_error_embed(self, ctx, message: str, **kwargs):
        embed = discord.Embed(
            color=discord.Colour.red(),
            description=self.get_message(message, **kwargs))
        await ctx.send(embed=embed)

    async def send_survey_embed(self, channel, raw_message: str):
        embed = discord.Embed(
            color=discord.Colour.gold(),
            description=raw_message)
        msg = await channel.send(embed=embed)
        return msg


intents = discord.Intents.default()
intents.members = True
bot = CompaniesClient(case_insensitive=True, help_command=None, intents=intents)
bot.run(bot.cfg['Token'])


def log_uncaught_exceptions(exctype, value, tb):
    logging.error("Uncaught Exception:\n" +
                  f"Type: {exctype}\n" +
                  f"Value: {value}\n" +
                  f"Traceback: {tb}\n")


sys.excepthook = log_uncaught_exceptions
