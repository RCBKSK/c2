import discord
from discord import app_commands
from discord.ext import commands
import os
import logging
import time
import jwt
import json
import httpx
import asyncio
from datetime import datetime
from dsa_tracker import DSATracker, LOKAPledgeTracker
from dotenv import load_dotenv
from ability_codes import get_ability_name
from task_checker import TaskChecker

# Set up logging
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

from alliance_manager import AllianceManager


class LokBotApi:

    def __init__(self, token, skip_jwt=False):
        self.alliance_manager = None
        self.API_BASE_URL = 'https://lok-api-live.leagueofkingdoms.com/api/'
        self.opener = httpx.Client(headers={
            'Accept': '*/*',
            'Accept-Encoding': 'gzip, deflate, br',
            'Accept-Language': 'en-US,en;q=0.9',
            'Origin': 'https://play.leagueofkingdoms.com',
            'Referer': 'https://play.leagueofkingdoms.com/',
            'Sec-Fetch-Dest': 'empty',
            'Sec-Fetch-Mode': 'cors',
            'Sec-Fetch-Site': 'same-site',
            'User-Agent':
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/114.0',
            'X-Access-Token': token if token else ''
        },
                                   http2=True,
                                   base_url=self.API_BASE_URL)
        self.token = token
        self.last_requested_at = time.time()

    def post(self, url, json_data=None):
        if json_data is None:
            json_data = {}

        post_data = json.dumps(json_data, separators=(',', ':'))

        # Remove request cookie since it's not needed and may cause account ban
        self.opener.cookies.clear()

        response = self.opener.post(url, data={'json': post_data})
        self.last_requested_at = time.time()

        logger.info(f"HTTP Request: POST {url} {response.status_code}")

        try:
            json_response = response.json()
        except json.JSONDecodeError:
            logger.error(f"Invalid JSON response: {response.text}")
            raise

        if json_response.get('result'):
            return json_response

        err = json_response.get('err', {})
        code = err.get('code')

        if code == 'no_auth':
            raise Exception("Authentication failed")
        elif code == 'duplicated':
            time.sleep(2)  # Wait before retry
            return self.post(url, json_data)
        elif code == 'exceed_limit_packet':
            time.sleep(3600)  # Wait 1 hour before retry
            return self.post(url, json_data)
        elif code == 'not_online':
            raise Exception("Not online")

        raise Exception(f"API Error: {code}")

    def auth_login(self, email, password):
        """Improved authentication with better device info"""
        device_info = {
            "build": "global",
            "OS": "Windows 10",  # Updated from Mac OS X
            "country": "USA",
            "language": "English",
            "bundle": "",
            "version": "1.1800.165.247",  # Updated version
            "platform": "web",
            "pushId": "23c6117d-ce29-4491-a79f-3b1774d8c220"
        }

        data = {
            "authType": "email",
            "email": email,
            "password": password,
            "deviceInfo": device_info
        }

        try:
            res = self.post(
                'https://lok-api-live.leagueofkingdoms.com/api/auth/login',
                data)
            if res.get('result'):
                self.token = res.get('token')
                self.opener.headers['x-access-token'] = self.token
                logger.info("Authentication successful")
            return res
        except Exception as e:
            logger.error(f"Auth failed: {str(e)}")
            raise Exception("Authentication failed - check credentials")

    def shrine_title(self):
        """Get all title statuses"""
        return self.post('shrine/title')

    def shrine_title_change(self, code, target_kingdom_id):
        data = {"code": code, "targetKingdomId": target_kingdom_id}
        logger.info(
            f"Making title change request with data: {json.dumps(data, indent=2)}"
        )
        return self.post('shrine/title/change', data)


def get_valid_token():
    """Get valid token through email auth with better error handling"""
    email = os.getenv('LOK_EMAIL')
    password = os.getenv('LOK_PASSWORD')

    logger.info(f"Attempting login with email: {email[:3]}***{email[-3:]}")

    if not email or not password:
        logger.error("LOK_EMAIL and LOK_PASSWORD must be set in environment")
        return None

    try:
        logger.info("Initializing API client...")
        api = LokBotApi("", True)

        logger.info("Making auth request...")
        auth_result = api.auth_login(email, password)

        logger.info(f"Auth response received: {auth_result}")

        if not auth_result.get('result'):
            logger.error(f"Login failed with response: {auth_result}")
            return None

        token = auth_result.get('token')
        logger.info(
            f"Login successful, token length: {len(token) if token else 0}")
        return token

    except Exception as e:
        logger.error(f"Error getting token: {str(e)}")
        logger.exception("Full exception details:")
        return None


# Global variables
api_client = None
title_cooldowns = {}

class RateLimitHandler:
    def __init__(self):
        self.last_reset = time.time()
        self.remaining = 5
        self.reset_after = 5.0
        self.retry_count = 0
        self.invalid_requests = 0
        self.invalid_reset = time.time()
        
    async def handle_rate_limit(self, e):
        # Check if it's a shared rate limit
        if hasattr(e.response, 'headers'):
            scope = e.response.headers.get('X-RateLimit-Scope')
            if scope == 'shared':
                logger.info("Shared rate limit - not counting against quota")
                await asyncio.sleep(1)
                return

            self.remaining = int(e.response.headers.get('X-RateLimit-Remaining', 0))
            self.reset_after = float(e.response.headers.get('X-RateLimit-Reset-After', 5.0))
        
        self.retry_count += 1
        # Progressive backoff with jitter
        base_wait = self.reset_after * (2 ** self.retry_count)
        jitter = random.uniform(0, 0.1 * base_wait)
        wait_time = min(base_wait + jitter, 3600)  # Max 1 hour
        
        logger.warning(f"Rate limited, waiting {wait_time:.2f} seconds before retry...")
        await asyncio.sleep(wait_time)

    async def handle_error(self, status_code: int):
        now = time.time()
        # Reset invalid request counter every 10 minutes
        if now - self.invalid_reset > 600:
            self.invalid_requests = 0
            self.invalid_reset = now
            
        if status_code in (401, 403, 429):
            self.invalid_requests += 1
            if self.invalid_requests > 9000:  # Safety threshold before CF ban
                wait_time = self.invalid_reset + 600 - now
                logger.error(f"Approaching invalid request limit! Waiting {wait_time:.2f}s")
                await asyncio.sleep(wait_time)
                
        if status_code == 401:
            logger.error("Invalid token detected - stopping requests")
            raise Exception("Authentication failed - token invalid")
        elif status_code == 403:
            logger.error("Permission denied - check bot permissions")
            await asyncio.sleep(5)  # Brief pause before retry
        
    def reset(self):
        self.retry_count = 0
        self.last_reset = time.time()

rate_limiter = RateLimitHandler()

# Helper functions
def format_time_period(hours):
    """Format hours into a readable time period (days, hours, minutes)"""
    if hours >= 24:
        days = int(hours // 24)
        remaining_hours = int(hours % 24)
        if remaining_hours > 0:
            return f"{days}d {remaining_hours}h"
        return f"{days}d"
    elif hours >= 1:
        minutes = int((hours % 1) * 60)
        if minutes > 0:
            return f"{int(hours)}h {minutes}m"
        return f"{int(hours)}h"
    else:
        return f"{int(hours * 60)}m"

# Discord bot setup
intents = discord.Intents.default()
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)


def is_admin(interaction: discord.Interaction):
    if isinstance(interaction.user, discord.Member):
        ALLOWED_ROLE_ID = 1191748616247844965
        has_role = any(role.id == ALLOWED_ROLE_ID
                       for role in interaction.user.roles)
        return interaction.user.guild_permissions.administrator or has_role
    return False


async def fetch_drago_data(drago_id: str):
    """Fetches Drago data from the API."""
    url = "https://lok-nft.leagueofkingdoms.com/api/market/detail"
    payload = {"tokenId": drago_id}
    logger.info(f"Making request to Drago API with payload: {payload}")
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(url, json=payload)
            response.raise_for_status()  # Raise HTTPError for bad responses (4xx or 5xx)
            data = response.json()
            logger.info(f"Drago API response: {json.dumps(data, indent=2)}")
            return data
    except httpx.HTTPStatusError as e:
        logger.error(f"HTTP error fetching Drago data: {e}")
        return None
    except httpx.RequestError as e:
        logger.error(f"Error connecting to Drago API: {e}")
        return None
    except json.JSONDecodeError as e:
        logger.error(f"Invalid JSON response from Drago API: {e}")
        return None


@tree.command(name="drago", description="Fetch and display information about a Drago.")
@app_commands.describe(drago_id="The ID of the Drago to look up.")
async def drago_command(interaction: discord.Interaction, drago_id: str):
    # Check if the command is used in the allowed channel
    ALLOWED_CHANNEL_ID = int(os.getenv('DRAGO_CHANNEL_ID', '0'))

    if ALLOWED_CHANNEL_ID and interaction.channel_id != ALLOWED_CHANNEL_ID:
        await interaction.response.send_message(
            f"This command can only be used in <#{ALLOWED_CHANNEL_ID}>", 
            ephemeral=True
        )
        return

    await interaction.response.defer()  # Acknowledge the command

    logger.info(f"Fetching Drago data for ID: {drago_id}")
    drago_data = await fetch_drago_data(drago_id)

    # Additional logging for debugging
    if drago_data:
        logger.info(f"Drago data retrieved successfully for ID: {drago_id}")
        if "stats" in drago_data:
            logger.info(f"Stats found: {len(drago_data['stats'])} stat entries")
        if "bonus" in drago_data:
            logger.info(f"Bonuses found: {len(drago_data['bonus'])} bonus entries")
    else:
        logger.error(f"Failed to retrieve data for Drago ID: {drago_id}")

    if not drago_data or not drago_data.get("result"):
        return await interaction.followup.send(
            "Could not retrieve Drago data. Please check the ID and try again.",
            ephemeral=True
        )

    drago = drago_data["drago"]

    # Create beautiful embed with gradient color
    embed = discord.Embed(
        title=f"🐉 Drago #{drago.get('tokenId', 'Unknown')}",
        color=0x6e48aa  # Purple gradient color
    )

    # Set large drago image
    embed.set_image(url=f"https://lok-nft.leagueofkingdoms.com/api/card/drago/{drago_id}")

    # Helper function to format percentages
    def format_percent(value):
        return f"{int(float(value) * 100)}%"

    # 1. Basic Information Section
    basic_info = [
        f"**Owner:** `{drago.get('owner', 'Unknown')}`",
        f"**Breed Count:** `{drago.get('breed', '0')}`",
        f"**Fusion:** `{drago.get('fusion', '0')}`",
        f"**Legendary Parts:** `{drago.get('filter', {}).get('parts', {}).get('legendary', '0')}`",
        f"**Genesis:** `{'Yes' if drago.get('filter', {}).get('parts', {}).get('genesis') else 'No'}`"
    ]

    embed.add_field(
        name="📝 Basic Information",
        value="\n".join(basic_info),
        inline=False
    )

    # 2. Stats Section - use ability codes to get proper names
    if stats := drago_data.get("stats"):
        stats_text = []

        for stat in stats:
            ability = stat.get("ability", {})
            code = ability.get("code")

            # Get the actual ability name from the code
            stat_name = get_ability_name(code)

            value = ability.get("value", 0)
            bonus = ability.get("bonus", 0)

            stat_line = f"✨ **{stat_name}:** +{format_percent(value)}"
            if bonus > 0:
                stat_line += f" (+{format_percent(bonus)} bonus)"
            stats_text.append(stat_line)

        embed.add_field(
            name="⚔️ Stats",
            value="\n".join(stats_text) or "No stats available",
            inline=False
        )

    # 3. Bonuses Section - use ability codes to get proper names
    if bonuses := drago_data.get("bonus"):
        bonus_text = []
        for i, bonus in enumerate(bonuses, 1):
            code = bonus.get("code")
            # Get the actual bonus name from the code
            bonus_name = get_ability_name(code)
            value = bonus.get("value", 0)
            bonus_text.append(
                f"🌟 **Bonus {i}: {bonus_name}:** +{format_percent(value)}"
            )

        embed.add_field(
            name="✨ Special Bonuses",
            value="\n".join(bonus_text) or "No bonuses available",
            inline=False
        )

    # Add footer with timestamp
    embed.set_footer(text=f"Drago Information • {datetime.now().strftime('%Y-%m-%d %H:%M')}")

    await interaction.followup.send(embed=embed)

@tree.command(name="run", description="Start and login to the game")
async def start_bot(interaction: discord.Interaction):
    try:
        await interaction.response.defer(ephemeral=True)

        logger.info("Starting login process...")

        # Get fresh token through login
        token = get_valid_token()
        if not token:
            error_msg = "Could not get valid token. Please check LOK_EMAIL and LOK_PASSWORD in Secrets"
            logger.error(error_msg)
            await interaction.followup.send(f"❌ {error_msg}", ephemeral=True)
            return

        logger.info(f"Token obtained successfully (length: {len(token)})")

        # Initialize API with new token
        global api_client
        api_client = LokBotApi(token, {})
        api_client.alliance_manager = AllianceManager(api_client)
        logger.info("API client and Alliance Manager initialized")

        # Test the connection by entering kingdom
        try:
            result = api_client.post('kingdom/enter')
            logger.info(f"Kingdom enter response: {result}")

            if result.get('result'):
                success_msg = "✅ Successfully logged in to the game!"
                logger.info(success_msg)
                await interaction.followup.send(success_msg, ephemeral=True)
            else:
                error_msg = f"❌ Failed to enter kingdom. Response: {result}"
                logger.error(error_msg)
                await interaction.followup.send(error_msg, ephemeral=True)

        except Exception as kingdom_error:
            error_msg = f"❌ Error entering kingdom: {str(kingdom_error)}"
            logger.error(error_msg, exc_info=True)
            await interaction.followup.send(error_msg, ephemeral=True)

    except Exception as e:
        error_msg = f"❌ Critical error during login process: {str(e)}"
        logger.error(error_msg, exc_info=True)
        await interaction.followup.send(error_msg, ephemeral=True)


@tree.command(name="title_request",
              description="🎖️ Award a prestigious title to your kingdom")
@app_commands.describe(title="Select the title you wish to be awarded",
                       kingdom_uid="Enter your kingdom UID")
@app_commands.choices(title=[
    app_commands.Choice(name="👷 Architect", value=108),
    app_commands.Choice(name="⚗️ Alchemist", value=109)
])
@app_commands.checks.cooldown(1, 300, key=lambda i: (i.guild_id, i.user.id))
@app_commands.checks.dynamic_cooldown(lambda i: app_commands.Cooldown(1, 60))  # 1 request per minute
@commands.max_concurrency(2, per=commands.BucketType.default, wait=True)
async def award_title(interaction: discord.Interaction,
                      title: app_commands.Choice[int], kingdom_uid: str):
    try:
        await interaction.response.defer(ephemeral=True)

        # Initialize API client if needed
        global api_client
        try:
            logger.info("=== Title Request Details ===")
            logger.info(
                f"User: {interaction.user.name} (ID: {interaction.user.id})")
            logger.info(
                f"User Roles: {[role.name for role in interaction.user.roles]}"
            )
            logger.info(
                f"Channel: {interaction.channel.name} (ID: {interaction.channel.id})"
            )
            logger.info(
                f"Guild: {interaction.guild.name} (ID: {interaction.guild.id})"
            )
            logger.info(f"Title Requested: {title.name} (Code: {title.value})")
            logger.info(f"Target Kingdom UID: {kingdom_uid}")
            logger.info("===========================")

            if not api_client:
                token = get_valid_token()
                if not token:
                    await interaction.followup.send(
                        "Could not get valid token. Please check credentials.",
                        ephemeral=True)
                    return
                api_client = LokBotApi(token)

            # Try to use existing client
            result = api_client.shrine_title_change(title.value, kingdom_uid)
        except Exception as e:
            # If token expired, try to get new one
            if "no_auth" in str(e).lower():
                token = get_valid_token()
                if not token:
                    await interaction.followup.send(
                        "Could not refresh token. Please check credentials.",
                        ephemeral=True)
                    return
                api_client = LokBotApi(token)
                result = api_client.shrine_title_change(
                    title.value, kingdom_uid)
            else:
                raise
        logger.info(
            f"Title change API response: {json.dumps(result, indent=2)}")
        success = result.get('result', False)

        embed = discord.Embed()
        if success:
            title_cooldowns[title.value] = time.time()
            embed.title = "🎖️ Title Awarded"
            embed.description = f"{interaction.user.display_name} has been awarded the title {title.name}.\nPlease use your expertise to benefit the continent."
            embed.color = discord.Color.green()
        else:
            err = result.get('err', {})
            if err:
                reason = str(err)
            else:
                reason = "Title could not be awarded. Please verify the kingdom UID is correct and try again."
            embed.title = "❌ Title Award Failed"
            embed.description = f"Failed to award title {title.name}\nReason: {reason}"
            embed.color = discord.Color.red()

        embed.add_field(name="Title", value=title.name, inline=True)
        embed.add_field(name="Awarded To",
                        value=interaction.user.display_name,
                        inline=True)

        # Add cooldown info
        if title.value in title_cooldowns:
            time_diff = int(300 - (time.time() - title_cooldowns[title.value]))
            if time_diff > 0:
                embed.add_field(name="Cooldown", value="5 Mins", inline=False)

        # Send the title response in the channel (visible to everyone)
        await interaction.channel.send(embed=embed)
        # Send a confirmation only visible to command user
        await interaction.followup.send("Title request processed!", ephemeral=True)

    except Exception as e:
        logger.error(f"Error awarding title: {str(e)}")
        embed = discord.Embed(title="⚠️ Error",
                              description=f"Error awarding title: {str(e)}",
                              color=discord.Color.orange())
        await interaction.followup.send(embed=embed, ephemeral=True)


@tree.error
async def on_command_error(interaction: discord.Interaction,
                           error: app_commands.AppCommandError):
    if isinstance(error, app_commands.CommandOnCooldown):
        await interaction.response.send_message(
            f"Please wait {error.retry_after:.2f} seconds before requesting another title.",
            ephemeral=True)


# Global variables for alliance status
status_message = None
status_channel_id = int(os.getenv('STATUS_CHANNEL_ID', '0')) or None


async def create_status_embed():
    if not api_client or not api_client.alliance_manager:
        return None

    status = await api_client.alliance_manager.get_alliance_status()

    total_power = status.get('total_power', 0)
    total_power_formatted = f"{total_power:,}"
    total_members = status.get('total_members', 0)
    online_members = status.get('online_count', 0)

    # Calculate online percentage
    online_percent = round((online_members / total_members * 100), 1) if total_members > 0 else 0

    # Create a visually appealing embed with a gold theme for alliance
    embed = discord.Embed(
        title="🏰 __**ALLIANCE STATUS**__ 🏰",
        description=f"```yaml\nLast Updated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n```",
        color=discord.Color.gold()
    )

    # Add alliance icon if available
    # embed.set_thumbnail(url="https://i.imgur.com/example_alliance_logo.png")

    # Overview section with emojis
    embed.add_field(
        name="📊 __Overview__",
        value=(
            f"👥 **Total Members:** `{total_members}`\n"
            f"🟢 **Online Members:** `{online_members}` (`{online_percent}%`)\n"
            f"⚔️ **Total Power:** `{total_power_formatted}`\n"
        ),
        inline=False
    )

    # Rank Distribution - make it visually appealing with progress bars and emojis
    ranks = status.get('ranks', {})
    if ranks:
        rank_details = []

        # Sort ranks by ID (assuming rank names like "Rank 1", "Rank 2", etc.)
        sorted_ranks = sorted(ranks.items(), 
                              key=lambda x: 99 if x[0] == "Leader" else int(x[0].split()[-1]))

        for rank_name, rank_data in sorted_ranks:
            # Create visual indicators for online percentage
            online_pct = rank_data['online_percent']
            bar_count = int(online_pct / 10)  # 10% per character in bar
            online_bar = "▰" * bar_count + "▱" * (10 - bar_count)

            # Format average power with commas
            avg_power_formatted = f"{rank_data['avg_power']:,}"

            # Use different emoji for leader rank
            rank_emoji = "👑" if rank_name == "Leader" else "🛡️"

            line = (
                f"{rank_emoji} **{rank_name}**\n"
                f"└ Members: `{rank_data['members']}` | Online: `{rank_data['online']}` | Power: `{avg_power_formatted}`\n"
                f"└ Online: `{online_bar}` `{online_pct}%`\n"
            )
            rank_details.append(line)

        embed.add_field(
            name="📋 __Rank Distribution__",
            value="\n".join(rank_details),
            inline=False
        )

    # Auto-remove status information if it's enabled
    if api_client.alliance_manager:
        auto_remove_status = "Enabled" if api_client.alliance_manager.auto_remove_enabled else "Disabled"
        # Calculate the actual threshold from seconds to hours
        threshold_hours = api_client.alliance_manager.offline_threshold / (60 * 60)

        # Format threshold in a readable way
        if threshold_hours >= 24:
            days = int(threshold_hours // 24)
            remaining_hours = int(threshold_hours % 24)
            threshold_str = f"{days} days"
            if remaining_hours > 0:
                threshold_str += f" {remaining_hours} hours"
        else:
            threshold_str = f"{int(threshold_hours)} hours"

        auto_remove_emoji = "✅" if api_client.alliance_manager.auto_remove_enabled else "❌"

        embed.add_field(
            name="⚙️ __Auto-Remove Settings__",
            value=f"{auto_remove_emoji} **Status:** `{auto_remove_status}`\n└ **Threshold:** `{threshold_str}`\n└ **Target:** `Rank 1 Members Only`",
            inline=False
        )

    # Add a footer
    embed.set_footer(text="Alliance Management System | Updated every 2 minutes")

    return embed


async def update_status_message():
    global status_message
    if not status_channel_id:
        return

    channel = client.get_channel(status_channel_id)
    if not channel:
        logger.warning(f"Status channel {status_channel_id} not found")
        return

    embed = await create_status_embed()
    if not embed:
        logger.warning("Failed to create status embed")
        return

    try:
        # If we don't have a cached status message, try to find it in pinned messages
        if not status_message:
            try:
                pins = await channel.pins()
                for pin in pins:
                    if pin.author == client.user and pin.embeds and "ALLIANCE STATUS" in pin.embeds[0].title:
                        status_message = pin
                        logger.info("Found existing pinned alliance status message")
                        break
            except Exception as pin_error:
                logger.error(f"Error checking pins: {pin_error}")

        # Update existing message or create a new one
        if status_message:
            await status_message.edit(embed=embed)
            logger.info("Updated existing alliance status message")
        else:
            # Send a new message with a header to make it stand out
            header = "━━━━━━━━━━━━━━━ **ALLIANCE STATUS MONITOR** ━━━━━━━━━━━━━━━"
            status_message = await channel.send(content=header, embed=embed)
            await status_message.pin()
            logger.info("Created and pinned new alliance status message")

            # Clean up other pins by the same bot if there are too many
            try:
                pins = await channel.pins()
                bot_pins = [p for p in pins if p.author == client.user and p.id != status_message.id]
                if len(bot_pins) > 2:  # Keep only the newest pins
                    for old_pin in bot_pins[2:]:
                        await old_pin.unpin()
                        logger.info(f"Unpinned old status message {old_pin.id}")
            except Exception as cleanup_error:
                logger.error(f"Error cleaning up pins: {cleanup_error}")

    except Exception as e:
        logger.error(f"Error updating status message: {e}")
        status_message = None  # Reset so we'll try to find it again next time


@tree.command(name="set_status_channel",
              description="Set channel for alliance status updates")
@app_commands.default_permissions(administrator=True)
async def set_status_channel(interaction: discord.Interaction):
    global status_channel_id
    status_channel_id = interaction.channel_id
    await interaction.response.send_message(
        "✅ This channel will now receive alliance status updates every 2 minutes.",
        ephemeral=True)
    await update_status_message()


@tree.command(name="alliance_status",
              description="Get current alliance status")
async def get_alliance_status(interaction: discord.Interaction):
    if not api_client or not api_client.alliance_manager:
        await interaction.response.send_message("Bot not properly initialized",
                                                ephemeral=True)
        return

    await interaction.response.defer()
    embed = await create_status_embed()
    if embed:
        await interaction.followup.send(embed=embed)
    else:
        await interaction.followup.send("Failed to get alliance status",
                                        ephemeral=True)


@tree.command(name="toggle_auto_accept",
              description="Toggle auto-accept for alliance join requests")
@app_commands.describe(enabled="Enable or disable auto-accept")
async def toggle_auto_accept(interaction: discord.Interaction, enabled: bool):
    if not is_admin(interaction):
        await interaction.response.send_message(
            "You don't have permission to use this command", ephemeral=True)
        return

    if not api_client or not api_client.alliance_manager:
        await interaction.response.send_message("Bot not properly initialized",
                                                ephemeral=True)
        return

    result = api_client.alliance_manager.toggle_auto_accept(enabled)
    status = "enabled" if result["enabled"] else "disabled"
    await interaction.response.send_message(f"Auto-accept has been {status}")


@tree.command(name="toggle_auto_remove",
              description="Toggle auto-remove for inactive R1 alliance members")
@app_commands.describe(
    enabled="Enable or disable auto-remove",
    hours="Number of hours of inactivity before removal (default: 2)")
async def toggle_auto_remove(interaction: discord.Interaction,
                             enabled: bool,
                             hours: int = 2):
    if not is_admin(interaction):
        await interaction.response.send_message(
            "You don't have permission to use this command", ephemeral=True)
        return

    if not api_client or not api_client.alliance_manager:
        await interaction.response.send_message("Bot not properly initialized",
                                                ephemeral=True)
        return

    # Convert hours to seconds
    api_client.alliance_manager.offline_threshold = hours * 60 * 60
    result = api_client.alliance_manager.toggle_auto_remove(enabled)
    status = "enabled" if result["enabled"] else "disabled"

    # Format time in a readable way
    if hours >= 24:
        days = hours // 24
        remaining_hours = hours % 24
        time_str = f"{days} days"
        if remaining_hours > 0:
            time_str += f" {remaining_hours} hours"
    else:
        time_str = f"{hours} hours"

    await interaction.response.send_message(
        f"Auto-remove has been {status} with {time_str} threshold")


@tree.command(name="send_mail",
              description="Send mail to all alliance members")
@app_commands.describe(
    subject="Mail subject",
    content="Mail content/message"
)
async def send_alliance_mail(interaction: discord.Interaction, subject: str, content: str):
    if not is_admin(interaction):
        await interaction.response.send_message(
            "You don't have permission to use this command", ephemeral=True)
        return

    if not api_client:
        await interaction.response.send_message("Bot not properly initialized",
                                                ephemeral=True)
        return

    await interaction.response.defer()

    try:
        # Send mail through API
        result = api_client.post('mail/send', {
            "subject": subject,
            "content": content
        })

        if result.get('result'):
            embed = discord.Embed(
                title="✉️ Alliance Mail Sent",
                description="Mail has been sent to all alliance members",
                color=discord.Color.green()
            )
            embed.add_field(name="Subject", value=subject)
            embed.add_field(name="Content", value=content)
            await interaction.followup.send(embed=embed)
        else:
            error = result.get('err', {}).get('message', 'Unknown error')
            await interaction.followup.send(f"❌ Failed to send mail: {error}", ephemeral=True)

    except Exception as e:
        logger.error(f"Error sending alliance mail: {str(e)}")
        await interaction.followup.send(f"❌ Error sending mail: {str(e)}", ephemeral=True)

@tree.command(name="update_loka_pledge",
              description="Manually update the LOKA pledge channel")
async def update_loka(interaction: discord.Interaction):
    if not is_admin(interaction):
        await interaction.response.send_message(
            "You don't have permission to use this command", ephemeral=True)
        return

    LOKA_CHANNEL_ID = int(os.getenv('LOKA_CHANNEL_ID', '0'))
    if not LOKA_CHANNEL_ID:
        await interaction.response.send_message(
            "LOKA_CHANNEL_ID not set in environment variables", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)

    try:
        loka_tracker = LOKAPledgeTracker()
        pledge_amount = await loka_tracker.get_loka_pledge()

        if pledge_amount is not None:
            channel = client.get_channel(LOKA_CHANNEL_ID)
            if channel:
                # Format the pledge amount with commas for thousands
                formatted_amount = "{:,}".format(pledge_amount)
                new_name = f"💰 LOKA Pledged : {formatted_amount}"

                try:
                    await channel.edit(name=new_name)
                    await interaction.followup.send(
                        f"✅ Successfully updated LOKA pledge channel to: {new_name}", 
                        ephemeral=True
                    )
                except Exception as e:
                    await interaction.followup.send(
                        f"❌ Error updating channel name: {e}", 
                        ephemeral=True
                    )
            else:
                await interaction.followup.send(
                    f"❌ Could not find channel with ID: {LOKA_CHANNEL_ID}", 
                    ephemeral=True
                )
        else:
            await interaction.followup.send(
                "❌ Failed to fetch LOKA pledge data", 
                ephemeral=True
            )
    except Exception as e:
        await interaction.followup.send(
            f"❌ Error: {e}", 
            ephemeral=True
        )


@tree.command(name="check_auto_remove",
              description="Check inactive alliance members")
async def check_auto_remove(interaction: discord.Interaction):
    if not is_admin(interaction):
        await interaction.response.send_message(
            "You don't have permission to use this command", ephemeral=True)
        return

    if not api_client or not api_client.alliance_manager:
        await interaction.response.send_message("Bot not properly initialized",
                                                ephemeral=True)
        return

    await interaction.response.defer()

    # Get current status
    auto_remove_status = "Enabled" if api_client.alliance_manager.auto_remove_enabled else "Disabled"
    threshold_hours = api_client.alliance_manager.offline_threshold / (60 * 60)

    # Format threshold in a readable way
    if threshold_hours >= 24:
        days = int(threshold_hours // 24)
        remaining_hours = int(threshold_hours % 24)
        threshold_str = f"{days} days"
        if remaining_hours > 0:
            threshold_str += f" {remaining_hours} hours"
    else:
        threshold_str = f"{int(threshold_hours)} hours"

    # Check for inactive members
    inactive_members = await api_client.alliance_manager.check_inactive_players()

    embed = discord.Embed(
        title="Alliance Auto-Remove Status",
        color=discord.Color.blue() if api_client.alliance_manager.auto_remove_enabled else discord.Color.red()
    )

    embed.add_field(name="Status", value=auto_remove_status, inline=True)
    embed.add_field(name="Threshold", value=threshold_str, inline=True)

    if inactive_members:
        removed = [r for r in inactive_members if r['removed']]
        failed = [r for r in inactive_members if not r['removed']]

        if removed:
            removed_text = "\n".join(f"✅ {r['name']} (Power: {r['power']:,}, Offline: {format_time_period(r['offline_hours'])})" for r in removed[:10])
            if len(removed) > 10:
                removed_text += f"\n...and {len(removed) - 10} more"
            embed.add_field(name=f"Removed Members ({len(removed)})", value=removed_text, inline=False)

        if failed:
            failed_text = "\n".join(f"❌ {f['name']} - {f['reason'] or 'Unknown error'}" for f in failed[:10])
            if len(failed) > 10:
                failed_text += f"\n...and {len(failed) - 10} more"
            embed.add_field(name=f"Failed Removals ({len(failed)})", value=failed_text, inline=False)
    else:
        embed.add_field(name="Inactive Members", value="No inactive members found", inline=False)

    await interaction.followup.send(embed=embed)


@client.event
async def on_ready():
    await tree.sync()
    logger.info(f"Title Bot is ready! Logged in as {client.user}")

    # Start background tasks
    logger.info("Starting background tasks...")
    client.loop.create_task(status_update_loop())
    client.loop.create_task(check_alliance_requests())
    client.loop.create_task(update_dsa_spawn_channel())
    client.loop.create_task(update_loka_pledge_channel())
    client.loop.create_task(check_tasks_loop())
    logger.info("Background tasks started")

async def check_tasks_loop():
    """Check kingdom tasks every 15 minutes"""
    task_checker = TaskChecker()
    
    while True:
        try:
            if api_client:
                tasks = await task_checker.check_tasks(api_client)
                if tasks:
                    logger.info(f"Found {len(tasks)} kingdom tasks")
                    # Process tasks here if needed
                    
            await asyncio.sleep(900)  # 15 minutes
        except Exception as e:
            logger.error(f"Error in task checker loop: {e}")
            await asyncio.sleep(60)  # Wait 1 minute on error before retrying

async def update_dsa_spawn_channel():
    """Update DSA spawn information in voice channel"""
    DSA_CHANNEL_ID = int(os.getenv('DSA_CHANNEL_ID', '0'))
    if not DSA_CHANNEL_ID:
        logger.warning("DSA_CHANNEL_ID not set in environment variables")
        return

    # Check if bot has access to the channel
    channel = client.get_channel(DSA_CHANNEL_ID)
    if not channel:
        logger.error(f"Could not find channel with ID {DSA_CHANNEL_ID}")
        return

    # Check if bot has permission to modify channel
    if not channel.permissions_for(channel.guild.me).manage_channels:
        logger.error(f"Bot does not have permission to modify channel {channel.name}")
        return

    dsa_tracker = DSATracker()

    while True:
        try:
            spawn_amount = await dsa_tracker.get_dsa_spawn()
            if spawn_amount is not None:
                channel = client.get_channel(DSA_CHANNEL_ID)
                if channel:
                    new_name = f"🐉 DSA Spawn : {spawn_amount}"
                    if channel.name != new_name:
                        try:
                            await channel.edit(name=new_name)
                            logger.info(f"Updated DSA spawn channel: {new_name}")
                        except discord.Forbidden as e:
                            logger.error(f"Bot lacks permission to edit channel: {e}")
                        except Exception as e:
                            logger.error(f"Error updating channel name: {e}")
                else:
                    logger.error(f"Could not find channel with ID: {DSA_CHANNEL_ID}")
            else:
                logger.error("No spawn amount received from API")
        except Exception as e:
            logger.error(f"Error updating DSA spawn channel: {e}")

        # Update every 2 hours to keep info fresh while respecting rate limits
        await asyncio.sleep(7200)


async def update_loka_pledge_channel():
    """Update LOKA pledge information in voice channel"""
    LOKA_CHANNEL_ID = int(os.getenv('LOKA_CHANNEL_ID', '0'))
    if not LOKA_CHANNEL_ID:
        logger.warning("LOKA_CHANNEL_ID not set in environment variables")
        return

    # Check if bot has access to the channel
    channel = client.get_channel(LOKA_CHANNEL_ID)
    if not channel:
        logger.error(f"Could not find channel with ID {LOKA_CHANNEL_ID}")
        return

    # Check if bot has permission to modify channel
    if not channel.permissions_for(channel.guild.me).manage_channels:
        logger.error(f"Bot does not have permission to modify channel {channel.name}")
        return

    loka_tracker = LOKAPledgeTracker()

    while True:
        try:
            pledge_amount = await loka_tracker.get_loka_pledge()
            if pledge_amount is not None:
                channel = client.get_channel(LOKA_CHANNEL_ID)
                if channel:
                    # Format the pledge amount with commas for thousands
                    formatted_amount = "{:,}".format(int(pledge_amount))
                    new_name = f"💰 LOKA Pledged : {formatted_amount}"

                    if channel.name != new_name:
                        try:
                            await channel.edit(name=new_name)
                            logger.info(f"Updated LOKA pledge channel: {new_name}")
                        except discord.Forbidden as e:
                            logger.error(f"Bot lacks permission to edit channel: {e}")
                        except Exception as e:
                            logger.error(f"Error updating channel name: {e}")
                else:
                    logger.error(f"Could not find channel with ID: {LOKA_CHANNEL_ID}")
            else:
                logger.error("No pledge amount received from API")
        except Exception as e:
            logger.error(f"Error updating LOKA pledge channel: {e}")

        # Update every 2 hours to keep info fresh while respecting rate limits
        await asyncio.sleep(7200)


async def status_update_loop():
    """Update alliance status message periodically"""
    logger.info("Alliance status update loop started")
    base_interval = 300  # 5 minutes base interval
    current_interval = base_interval
    
    while True:
        try:
            # Add jitter to prevent synchronized requests
            jitter = random.uniform(-30, 30)
            effective_interval = current_interval + jitter
            
            logger.info("Updating alliance status message...")
            try:
                await update_status_message()
                rate_limiter.reset()
                current_interval = base_interval
                await asyncio.sleep(max(effective_interval, 60))  # Minimum 1 minute
            except discord.errors.HTTPException as e:
                await rate_limiter.handle_error(e.status)
                if e.status == 429:  # Rate limit error
                    await rate_limiter.handle_rate_limit(e)
                    current_interval = min(current_interval * 2, 3600)
                elif e.status in (401, 403):
                    # Token invalid or permissions issue
                    await asyncio.sleep(300)  # Longer pause for auth issues
                else:
                    raise
            logger.info("Alliance status message updated")

            # Check auto-remove status
            if api_client and api_client.alliance_manager:
                auto_remove_status = "Enabled" if api_client.alliance_manager.auto_remove_enabled else "Disabled"
                threshold_hours = api_client.alliance_manager.offline_threshold / (60 * 60)

                # Format threshold in a readable way
                if threshold_hours >= 24:
                    days = int(threshold_hours // 24)
                    remaining_hours = int(threshold_hours % 24)
                    threshold_str = f"{days} days"
                    if remaining_hours > 0:
                        threshold_str += f" {remaining_hours} hours"
                else:
                    threshold_str = f"{int(threshold_hours)} hours"

                logger.info(f"Auto-remove status: {auto_remove_status}, Threshold: {threshold_str}")

                if api_client.alliance_manager.auto_remove_enabled:
                    logger.info("Checking for inactive members...")
                    results = await api_client.alliance_manager.check_inactive_players()
                    if results:
                        removed = [r for r in results if r['removed']]
                        failed = [r for r in results if not r['removed']]

                        if removed:
                            logger.info(f"Removed {len(removed)} inactive members")
                            for r in removed:
                                offline_time = format_time_period(r['offline_hours'])
                                logger.info(f"Removed {r['name']} (Power: {r['power']:,}, Offline: {offline_time})")

                        if failed:
                            logger.warning(f"Failed to remove {len(failed)} members")
                            for f in failed:
                                logger.warning(f"Failed to remove {f['name']}: {f['reason'] or 'Unknown error'}")
                    else:
                        logger.info("No inactive members found")
        except Exception as e:
            logger.error(f"Error in status update loop: {str(e)}")
        await asyncio.sleep(120)  # Update every 2 minutes


async def check_alliance_requests():
    """Check and process alliance join requests"""
    while True:
        try:
            if not api_client or not api_client.alliance_manager:
                await asyncio.sleep(60)
                continue

            webhook_url = os.getenv('ALLIANCE_REQUEST_WEBHOOK')
            if not webhook_url:
                logger.warning(
                    "No webhook URL configured for alliance requests")
                await asyncio.sleep(120)
                continue

            # Get current requests
            logger.info("Checking for alliance join requests...")
            response = api_client.post('alliance/request/list')

            if not response.get('result'):
                logger.warning("Failed to get request list from API")
                await asyncio.sleep(120)
                continue

            request_list = response.get('requestList', [])
            logger.info(f"Found {len(request_list)} join requests")

            # Process auto-accept if enabled
            accept_results = []
            if api_client.alliance_manager.auto_accept_enabled and request_list:
                logger.info("Auto-accept is enabled, processing requests...")

                for request in request_list:
                    result = {
                        'accepted': False,
                        'name': request.get('name', 'Unknown'),
                        'power': int(request.get('power', 0)),
                        'kingdom': request.get('kingdomName', 'Unknown'),
                        'reason': '',
                        'kingdom_id': request.get('_id')
                    }

                    try:
                        accept_response = api_client.post(
                            'alliance/request/accept',
                            {"kingdomId": request.get('_id')})

                        if accept_response.get('result'):
                            result['accepted'] = True
                            logger.info(
                                f"Accepted request from {result['name']}")
                        else:
                            error = accept_response.get('err', {})
                            result['reason'] = error.get(
                                'message', 'Unknown error')
                            logger.warning(
                                f"Failed to accept {result['name']}: {result['reason']}"
                            )

                    except Exception as e:
                        result['reason'] = str(e)
                        logger.error(f"Error accepting request: {str(e)}")

                    accept_results.append(result)

            # Send webhook notification only if there are requests or auto-accept results
            if webhook_url and (request_list or accept_results):
                embed = discord.Embed(title="🤖 Alliance Join Requests",
                                      color=0x5865F2,
                                      timestamp=datetime.now())

                # Process accepted requests
                if accept_results:
                    def format_power(power):
                        return f"{power / 1000000:.1f}M" if power >= 1000000 else f"{power:,}"

                    accepted = [r for r in accept_results if r['accepted']]
                    if accepted:
                        accepted_text = "\n".join(
                            f"✅ {r['name']} (Power: {format_power(r['power'])})"
                            for r in accepted)
                        embed.add_field(name="Auto-Accepted Members",
                                        value=accepted_text,
                                        inline=False)

                        # Create a list of accepted kingdom IDs to filter out from pending list
                        accepted_ids = {r.get('kingdom_id', '') for r in accepted}

                    failed = [r for r in accept_results if not r['accepted']]
                    if failed:
                        failed_text = "\n".join(
                            f"❌ {r['name']} - {r['reason'] or 'Unknown error'}"
                            for r in failed)
                        embed.add_field(name="Failed Accepts",
                                        value=failed_text,
                                        inline=False)
                else:
                    accepted_ids = set()

                # Only show pending requests that weren't just accepted
                pending_requests = [r for r in request_list if str(r.get('_id', '')) not in accepted_ids]
                if pending_requests:
                    pending_text = "\n".join(
                        f"⏳ {r.get('name', 'Unknown')} (Power: {format_power(int(r.get('power', 0)))})"
                        for r in pending_requests)
                    embed.add_field(name="Pending Requests",
                                    value=pending_text,
                                    inline=False)

                # Show rejected requests
                rejected = [r for r in accept_results if not r['accepted'] and 'Minimum Power Not Met' in r.get('reason', '')]
                if rejected:
                    rejected_text = "\n".join(
                        f"❌ {r['name']} (Power: {format_power(r['power'])}) - {r['reason']}"
                        for r in rejected)
                    embed.add_field(name="Rejected Requests",
                                    value=rejected_text,
                                    inline=False)

                try:
                    webhook = discord.Webhook.from_url(webhook_url,
                                                       client=client)
                    await webhook.send(embed=embed)
                    logger.info(f"Sent webhook with {len(request_list)} pending requests and {len(accept_results)} auto-accept results")
                except Exception as e:
                    logger.error(f"Failed to send webhook: {str(e)}")

        except Exception as e:
            logger.error(f"Error in alliance request check: {str(e)}")

        await asyncio.sleep(120)  # Check every 2 minutes


def run_http_server():
    """Run an HTTP server to keep the bot alive."""
    import http.server
    import threading
    import urllib.request
    import time

    class StatusHandler(http.server.BaseHTTPRequestHandler):

        def do_HEAD(self):
            self.send_response(200)
            self.send_header('Content-Type', 'text/plain')
            self.end_headers()

        def do_GET(self):
            self.send_response(200)
            self.send_header('Content-Type', 'text/plain')
            self.end_headers()
            self.wfile.write(b'Queen is Up\n')
            logger.info("Health check - Queen is Up")

        def log_message(self, format, *args):
            logger.info(f"HTTP: {format % args}")

    def health_check():
        while True:
            try:
                urllib.request.urlopen(f"http://0.0.0.0:{port}/").read()
            except Exception as e:
                logger.error(f"Health check failed: {e}")
            time.sleep(60)  # Check every minute

    port = int(os.environ.get('PORT', 10000))
    server_address = ('0.0.0.0', port)

    try:
        server = http.server.HTTPServer(server_address, StatusHandler)
        server_thread = threading.Thread(target=server.serve_forever)
        health_thread = threading.Thread(target=health_check)
        server_thread.daemon = True
        health_thread.daemon = True
        server_thread.start()
        health_thread.start()
        logger.info(f"HTTP server started on port {port}")
    except Exception as e:
        logger.critical(f"Failed to start HTTP server: {e}")


def run_title_bot():
    token = os.getenv("DISCORD_BOT_TOKEN")
    if not token:
        logger.error(
            "Discord bot token not found - set DISCORD_BOT_TOKEN in Secrets")
        return

    # Start HTTP server to keep the bot alive
    run_http_server()

    try:
        logger.info("Starting Title Bot")
        # Add retry logic with exponential backoff
        retry_count = 0
        while True:
            try:
                client.run(token)
                break
            except discord.errors.HTTPException as e:
                if e.status == 429:  # Rate limit error
                    retry_count += 1
                    wait_time = min(300 * (2 ** retry_count), 3600)  # Exponential backoff, max 1 hour
                    logger.warning(f"Rate limited, waiting {wait_time} seconds before retry...")
                    time.sleep(wait_time)
                else:
                    raise
    except Exception as e:
        logger.error(f"CRITICAL ERROR: Title bot crashed: {str(e)}")
        import traceback
        traceback.print_exc()

        # Keep HTTP server running even if bot crashes
        while True:
            time.sleep(60)
            logger.info("HTTP server still alive, waiting for restart...")


if __name__ == "__main__":
    run_title_bot()