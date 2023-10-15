import discord

from typing import TYPE_CHECKING, cast

from discord import app_commands
from discord.ext import commands

from ballsdex.settings import settings
from ballsdex.core.models import GuildConfig
from ballsdex.packages.config.components import AcceptTOSView

if TYPE_CHECKING:
    from ballsdex.core.bot import BallsDexBot

activation_embed = discord.Embed(
    colour=0x00D936,
    title=f"Įgalinti lietuvkamuolius",
    description=f"Kad įgalintumėte lietuvkamuolius savo serveryje, turite "
    f"perskaityti ir sutikti su [Paslaugų Teikimo Sąlygomis]({settings.terms_of_service}).\n\n"
    "Apibendrintai, čia yra šios programos taisyklės:\n"
    "- Draudžiama kurti serverius tik dėl lietuvkamuolių\n"
    "- Parduoti ar mainytis kamuolius už pinigus ar kitas prekes griežtai draudžiama\n"
    "- Nebandykite piktnaudžiauti su programos vidum\n"
    "**Šių taisyklių nepaisymas įtrauks jus į juodąjį sąrašą**",
)


@app_commands.default_permissions(manage_guild=True)
@app_commands.guild_only()
class Config(commands.GroupCog):
    """
    View and manage your countryballs collection.
    """

    def __init__(self, bot: "BallsDexBot"):
        self.bot = bot

    @app_commands.command()
    @app_commands.describe(channel="The new text channel to set.")
    async def channel(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
    ):
        """
        Set or change the channel where countryballs will spawn.
        """
        guild = cast(discord.Guild, interaction.guild)  # guild-only command
        user = cast(discord.Member, interaction.user)
        if not user.guild_permissions.manage_guild:
            await interaction.response.send_message(
                "You need the permission to manage the server to use this."
            )
            return
        if not channel.permissions_for(guild.me).read_messages:
            await interaction.response.send_message(
                f"I need the permission to read messages in {channel.mention}."
            )
            return
        if not channel.permissions_for(guild.me).send_messages:
            await interaction.response.send_message(
                f"I need the permission to send messages in {channel.mention}."
            )
            return
        if not channel.permissions_for(guild.me).embed_links:
            await interaction.response.send_message(
                f"I need the permission to send embed links in {channel.mention}."
            )
            return
        await interaction.response.send_message(
            embed=activation_embed, view=AcceptTOSView(interaction, channel)
        )

    @app_commands.command()
    async def disable(self, interaction: discord.Interaction):
        """
        Disable or enable countryballs spawning.
        """
        guild = cast(discord.Guild, interaction.guild)  # guild-only command
        user = cast(discord.Member, interaction.user)
        if not user.guild_permissions.manage_guild:
            await interaction.response.send_message(
                "You need the permission to manage the server to use this."
            )
            return
        config, created = await GuildConfig.get_or_create(guild_id=interaction.guild_id)
        if config.enabled:
            config.enabled = False  # type: ignore
            await config.save()
            self.bot.dispatch("ballsdex_settings_change", guild, enabled=False)
            await interaction.response.send_message(
                f"{settings.bot_name} dabar išjungti šiame serveryje. Komandos dar vis bus "
                f"įgalintos, bet kamuoliai nebeatsiradinės.\n"
                "Kad vėl įgalintumėte kamuolių atsiradinėjimą, panaudokite tą pačią komandą."
            )
        else:
            config.enabled = True  # type: ignore
            await config.save()
            self.bot.dispatch("ballsdex_settings_change", guild, enabled=True)
            if config.spawn_channel and (channel := guild.get_channel(config.spawn_channel)):
                await interaction.response.send_message(
                    f"{settings.bot_name} dabar įgalinti šiame serveryje, "
                    f"Kamuoliai netrukus pradės atsiradinėti {channel.mention}."
                )
            else:
                await interaction.response.send_message(
                    f"{settings.bot_name} dabar įgalinti šiame serveryje, bet nenustatytas joks "
                    "atsiradimo kanalas. Prašome nustatyti atsiradimo kanalą su `/config channel`."
                )
