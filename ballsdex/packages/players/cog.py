import discord
import logging
import enum

from typing import TYPE_CHECKING
from collections import defaultdict
from tortoise.exceptions import DoesNotExist

from discord import app_commands
from discord.ui import View, Button, button
from discord.ext import commands

from ballsdex.settings import settings
from ballsdex.core.models import Player, BallInstance, DonationPolicy, balls
from ballsdex.core.utils.paginator import FieldPageSource, Pages
from ballsdex.core.utils.transformers import BallInstanceTransform
from ballsdex.packages.players.countryballs_paginator import CountryballsViewer

if TYPE_CHECKING:
    from ballsdex.core.bot import BallsDexBot

log = logging.getLogger("ballsdex.packages.countryballs")


class DonationRequest(View):
    def __init__(
        self,
        bot: "BallsDexBot",
        interaction: discord.Interaction,
        countryball: BallInstance,
        new_player: Player,
    ):
        super().__init__(timeout=120)
        self.bot = bot
        self.original_interaction = interaction
        self.countryball = countryball
        self.new_player = new_player

    async def interaction_check(self, interaction: discord.Interaction, /) -> bool:
        if interaction.user.id != self.new_player.discord_id:
            await interaction.response.send_message(
                "Jums neleidžiama keisti to.", ephemeral=True
            )
            return False
        return True

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        try:
            await self.original_interaction.followup.edit_message("@original", view=self)
        except discord.NotFound:
            pass
        del self.bot.locked_balls[self.countryball.id]

    @button(
        style=discord.ButtonStyle.success, emoji="\N{HEAVY CHECK MARK}\N{VARIATION SELECTOR-16}"
    )
    async def accept(self, interaction: discord.Interaction, button: Button):
        self.stop()
        for item in self.children:
            item.disabled = True
        self.countryball.trade_player = self.countryball.player
        self.countryball.player = self.new_player
        await self.countryball.save()
        await interaction.response.edit_message(
            content=interaction.message.content
            + "\n\N{WHITE HEAVY CHECK MARK} Dovana buvo priimta!",
            view=self,
        )
        del self.bot.locked_balls[self.countryball.id]

    @button(
        style=discord.ButtonStyle.danger,
        emoji="\N{HEAVY MULTIPLICATION X}\N{VARIATION SELECTOR-16}",
    )
    async def deny(self, interaction: discord.Interaction, button: Button):
        self.stop()
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(
            content=interaction.message.content + "\n\N{CROSS MARK} Dovana buvo atmesta.",
            view=self,
        )
        del self.bot.locked_balls[self.countryball.id]


class SortingChoices(enum.Enum):
    alphabetic = "ball__country"
    catch_date = "-catch_date"
    rarity = "ball__rarity"
    special = "special__id"
    health_bonus = "-health_bonus"
    attack_bonus = "-attack_bonus"
    stats = "stats"

    # manual sorts are not sorted by SQL queries but by our code
    # this may be do-able with SQL still, but I don't have much experience ngl
    duplicates = "manualsort-duplicates"


class Players(commands.GroupCog, group_name=settings.players_group_cog_name):
    """
    View and manage your countryballs collection.
    """

    def __init__(self, bot: "BallsDexBot"):
        self.bot = bot

    @app_commands.command()
    @app_commands.checks.cooldown(1, 10, key=lambda i: i.user.id)
    async def list(
        self,
        interaction: discord.Interaction,
        user: discord.User | None = None,
        sort: SortingChoices | None = None,
        reverse: bool = False,
    ):
        """
        List your countryballs.

        Parameters
        ----------
        user: discord.User
            The user whose collection you want to view, if not yours.
        sort: SortingChoices
            Choose how countryballs are sorted. Can be used to show duplicates.
        reverse: bool
            Reverse the output of the list.
        """
        user: discord.User | discord.Member = user or interaction.user
        await interaction.response.defer(thinking=True)

        try:
            player = await Player.get(discord_id=user.id)
        except DoesNotExist:
            if user == interaction.user:
                await interaction.followup.send(
                    f"Dar neturite kamuolių."
                )
            else:
                await interaction.followup.send(
                    f"{user.name} dar neturi kamuolių."
                )
            return

        await player.fetch_related("balls")
        if sort:
            if sort == SortingChoices.duplicates:
                countryballs = await player.balls.all()
                count = defaultdict(int)
                for countryball in countryballs:
                    count[countryball.countryball.pk] += 1
                countryballs.sort(key=lambda m: (-count[m.countryball.pk], m.countryball.pk))
            elif sort == SortingChoices.stats:
                countryballs = await player.balls.all().order_by("-health_bonus", "-attack_bonus")
            else:
                countryballs = await player.balls.all().order_by(sort.value)
        else:
            countryballs = await player.balls.all().order_by("-favorite", "-shiny")

        if len(countryballs) < 1:
            if user == interaction.user:
                await interaction.followup.send(
                    f"Dar neturite kamuolių."
                )
            else:
                await interaction.followup.send(
                    f"{user.name} dar neturi kamuolių."
                )
            return
        if reverse:
            countryballs.reverse()

        paginator = CountryballsViewer(interaction, countryballs)
        if user == interaction.user:
            await paginator.start()
        else:
            await paginator.start(content=f"Žiūrit {user.name} kamuolius")

    @app_commands.command()
    @app_commands.checks.cooldown(1, 60, key=lambda i: i.user.id)
    async def completion(self, interaction: discord.Interaction, user: discord.User | None = None):
        """
        Show your current completion of the BallsDex.

        Parameters
        ----------
        user: discord.User
            The user whose completion you want to view, if not yours.
        """
        user = user or interaction.user
        # Filter disabled balls, they do not count towards progression
        # Only ID and emoji is interesting for us
        bot_countryballs = {x: y.emoji_id for x, y in balls.items() if y.enabled}

        if not bot_countryballs:
            await interaction.response.send_message(
                f"There are no {settings.collectible_name}s registered on this bot yet.",
                ephemeral=True,
            )
            return
        await interaction.response.defer(thinking=True)

        # Set of ball IDs owned by the player
        owned_countryballs = set(
            x[0]
            for x in await BallInstance.filter(player__discord_id=user.id, ball__enabled=True)
            .distinct()  # Do not query everything
            .values_list("ball_id")
        )

        entries: list[tuple[str, str]] = []

        def fill_fields(title: str, emoji_ids: set[int]):
            # check if we need to add "(continued)" to the field name
            first_field_added = False
            buffer = ""

            for emoji_id in emoji_ids:
                emoji = self.bot.get_emoji(emoji_id)
                if not emoji:
                    continue

                text = f"{emoji} "
                if len(buffer) + len(text) > 1024:
                    # hitting embed limits, adding an intermediate field
                    if first_field_added:
                        entries.append(("\u200B", buffer))
                    else:
                        entries.append((f"__**{title}**__", buffer))
                        first_field_added = True
                    buffer = ""
                buffer += text

            if buffer:  # add what's remaining
                if first_field_added:
                    entries.append(("\u200B", buffer))
                else:
                    entries.append((f"__**{title}**__", buffer))

        if owned_countryballs:
            # Getting the list of emoji IDs from the IDs of the owned countryballs
            fill_fields(
                f"Turimi lietuvkamuoliai",
                set(bot_countryballs[x] for x in owned_countryballs),
            )
        else:
            entries.append((f"__**Turimi lietuvkamuoliai**__", "Dar tuščia."))

        if missing := set(y for x, y in bot_countryballs.items() if x not in owned_countryballs):
            fill_fields(f"Trūkstami lietuvkamuoliai", missing)
        else:
            entries.append(
                (
                    f"__**:tada: SURINKOTE VISUS LIETUVKAMUOLIUS, "
                    "SVEIKINIMAI! :tada:**__",
                    "\u200B",
                )
            )  # force empty field value

        source = FieldPageSource(entries, per_page=5, inline=False, clear_description=False)
        source.embed.description = (
            f"Surinkti {settings.bot_name}:"
            f"**{round(len(owned_countryballs)/len(bot_countryballs)*100, 1)}%**"
        )
        source.embed.colour = discord.Colour.blurple()
        source.embed.set_author(name=user.display_name, icon_url=user.display_avatar.url)

        pages = Pages(source=source, interaction=interaction, compact=True)
        await pages.start()

    @app_commands.command()
    @app_commands.checks.cooldown(1, 5, key=lambda i: i.user.id)
    async def info(self, interaction: discord.Interaction, countryball: BallInstanceTransform):
        """
        Display info from a specific countryball.

        Parameters
        ----------
        countryball: BallInstance
            The countryball you want to inspect
        """
        if not countryball:
            return
        await interaction.response.defer(thinking=True)
        content, file = await countryball.prepare_for_message(interaction)
        await interaction.followup.send(content=content, file=file)
        file.close()

    @app_commands.command()
    @app_commands.checks.cooldown(1, 60, key=lambda i: i.user.id)
    async def last(self, interaction: discord.Interaction, user: discord.Member = None):
        """
        Display info of your or another users last caught countryball.

        Parameters
        ----------
        user: discord.Member
            The user you would like to see
        """
        user_obj = user if user else interaction.user
        await interaction.response.defer(thinking=True)
        try:
            player = await Player.get(discord_id=user_obj.id)
        except DoesNotExist:
            msg = f"{'You do' if user is None else f'{user_obj.display_name} does'}"
            await interaction.followup.send(
                f"{msg} not have any {settings.collectible_name} yet.",
                ephemeral=True,
            )
            return

        countryball = await player.balls.all().order_by("-id").first().select_related("ball")
        if not countryball:
            msg = f"{'You do' if user is None else f'{user_obj.display_name} does'}"
            await interaction.followup.send(
                f"{msg} not have any {settings.collectible_name} yet.",
                ephemeral=True,
            )
            return

        content, file = await countryball.prepare_for_message(interaction)
        await interaction.followup.send(content=content, file=file)
        file.close()

    @app_commands.command()
    async def favorite(self, interaction: discord.Interaction, countryball: BallInstanceTransform):
        """
        Set favorite countryballs.

        Parameters
        ----------
        countryball: BallInstance
            The countryball you want to set/unset as favorite
        """
        if not countryball:
            return

        if not countryball.favorite:
            player = await Player.get(discord_id=interaction.user.id).prefetch_related("balls")
            if await player.balls.filter(favorite=True).count() > 20:
                await interaction.response.send_message(
                    f"Galite turėti tik 20 mėgstamiausių kamuolių.",
                    ephemeral=True,
                )
                return

            countryball.favorite = True  # type: ignore
            await countryball.save()
            emoji = self.bot.get_emoji(countryball.countryball.emoji_id) or ""
            await interaction.response.send_message(
                f"{emoji} `#{countryball.pk:0X}` {countryball.countryball.country} "
                f"dabar mėgstamiausias kamuolys!",
                ephemeral=True,
            )

        else:
            countryball.favorite = False  # type: ignore
            await countryball.save()
            emoji = self.bot.get_emoji(countryball.countryball.emoji_id) or ""
            await interaction.response.send_message(
                f"{emoji} `#{countryball.pk:0X}` {countryball.countryball.country} "
                f"nebebėra mėgstamiausias kamuolys.",
                ephemeral=True,
            )

    @app_commands.command()
    @app_commands.choices(
        policy=[
            app_commands.Choice(name="Accept all donations", value=DonationPolicy.ALWAYS_ACCEPT),
            app_commands.Choice(
                name="Request your approval first", value=DonationPolicy.REQUEST_APPROVAL
            ),
            app_commands.Choice(name="Deny all donations", value=DonationPolicy.ALWAYS_DENY),
        ]
    )
    async def donation_policy(
        self, interaction: discord.Interaction, policy: app_commands.Choice[int]
    ):
        """
        Change how you want to receive donations from /balls give

        Parameters
        ----------
        policy: DonationPolicy
            The new policy for accepting donations
        """
        player, _ = await Player.get_or_create(discord_id=interaction.user.id)
        player.donation_policy = policy.value
        if policy.value == DonationPolicy.ALWAYS_ACCEPT:
            await interaction.response.send_message(
                f"Nustatymas pakeistas, dabar gausi visus kamuolius "
                "iškart."
            )
        elif policy.value == DonationPolicy.REQUEST_APPROVAL:
            await interaction.response.send_message(
                "Nustatymas pakeistas, dabar turėsi patvirtinti visas dovanas pats."
            )
        elif policy.value == DonationPolicy.ALWAYS_DENY:
            await interaction.response.send_message(
                f"Nustatymas pakeistas, kiti dabar nebegali naudoti {self.give.extras['mention']} su "
                "jumis. Kita vertus, įmanoma gauti dovanų per mainus."
            )
        else:
            await interaction.response.send_message("Invalid input!")
            return
        await player.save()  # do not save if the input is invalid

    @app_commands.command()
    async def give(
        self,
        interaction: discord.Interaction,
        user: discord.User,
        countryball: BallInstanceTransform,
    ):
        """
        Give a countryball to a user.

        Parameters
        ----------
        user: discord.User
            The user you want to give a countryball to
        countryball: BallInstance
            The countryball you're giving away
        """
        if not countryball:
            return
        if not countryball.countryball.tradeable:
            await interaction.response.send_message(
                "Jūs negalite dovanoti šio kamuolio", ephemeral=True
            )
            return
        if user.bot:
            await interaction.response.send_message("Negalite dovanoti kamuolių programoms.")
            return
        if countryball.id in self.bot.locked_balls:
            await interaction.response.send_message(
                "Šis kamuolys dabar užrakintas mainuose, bandykite vėliau."
            )
            return
        self.bot.locked_balls[countryball.id] = None
        new_player, _ = await Player.get_or_create(discord_id=user.id)
        old_player = countryball.player

        if new_player == old_player:
            await interaction.response.send_message(
                f"Negalite dovanoti kamuolio sau."
            )
            del self.bot.locked_balls[countryball.id]
            return
        if new_player.donation_policy == DonationPolicy.ALWAYS_DENY:
            await interaction.response.send_message(
                "Šis žmogus nepriima dovanų, vietoj to galite naudoti mainus."
            )
            del self.bot.locked_balls[countryball.id]
            return
        elif new_player.donation_policy == DonationPolicy.REQUEST_APPROVAL:
            await interaction.response.send_message(
                f"Ei, {user.mention}, {interaction.user.name} nori tau duoti "
                f"{countryball.description(include_emoji=True, bot=interaction.client)}!\n"
                "Ar priimi šią dovaną?",
                view=DonationRequest(self.bot, interaction, countryball, new_player),
            )
            return

        countryball.player = new_player
        countryball.trade_player = old_player
        countryball.favorite = False
        await countryball.save()

        await interaction.response.send_message(
            f"Ką tik padovanojai {user.mention} kamuolį "
            f"{countryball.description(short=True, include_emoji=True, bot=self.bot)} "
        )
        del self.bot.locked_balls[countryball.id]
