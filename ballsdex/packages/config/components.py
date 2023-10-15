import discord

from discord.ui import View, Button, button

from ballsdex.core.models import GuildConfig


class AcceptTOSView(View):
    """
    Button prompting the admin setting up the bot to accept the terms of service.
    """

    def __init__(self, interaction: discord.Interaction, channel: discord.TextChannel):
        super().__init__()
        self.original_interaction = interaction
        self.channel = channel
        self.add_item(
            Button(
                style=discord.ButtonStyle.link,
                label="Paslaugų Teikimo Sąlygos",
                url="https://gist.github.com",
            )
        )
        self.add_item(
            Button(
                style=discord.ButtonStyle.link,
                label="Privatumo politika",
                url="https://gist.github.com",
            )
        )

    @button(
        label="Sutikti",
        style=discord.ButtonStyle.success,
        emoji="\N{HEAVY CHECK MARK}\N{VARIATION SELECTOR-16}",
    )
    async def accept_button(self, interaction: discord.Interaction, item: discord.ui.Button):
        config, created = await GuildConfig.get_or_create(guild_id=interaction.guild_id)
        config.spawn_channel = self.channel.id  # type: ignore
        await config.save()
        interaction.client.dispatch(
            "ballsdex_settings_change", interaction.guild, channel=self.channel
        )
        self.stop()
        await interaction.response.send_message(
            f"Naujas atsiradimo kalanas dabar yra {self.channel.mention}.\n"
            "Kamuoliai atsiradinės kai žmonės kalbės, nebent programa yra išjungta."
        )

        self.accept_button.disabled = True
        try:
            await self.original_interaction.followup.edit_message("@original", view=self)
        except discord.HTTPException:
            pass

    async def on_timeout(self) -> None:
        self.stop()
        for item in self.children:
            item.disabled = True  # type: ignore
        try:
            await self.original_interaction.followup.edit_message("@original", view=self)
        except discord.HTTPException:
            pass
