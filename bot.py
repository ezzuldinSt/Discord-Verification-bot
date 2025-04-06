import discord
from discord import app_commands
from discord.ext import commands
import json
import os
import asyncio
from typing import Dict, List, Optional

# --- Configuration ---
# !! IMPORTANT !!
# 1. Replace 'YOUR_BOT_TOKEN' with your actual bot token.
#    Consider using environment variables for security: os.getenv('DISCORD_BOT_TOKEN')
BOT_TOKEN = os.getenv('DISCORD_BOT_TOKEN')
# 2. Make sure the bot has the required intents enabled in the Discord Developer Portal:
#    - SERVER MEMBERS INTENT
#    - MESSAGE CONTENT INTENT
# 3. Ensure the bot has the necessary permissions in your server:
#    - Send Messages
#    - Manage Roles
#    - Read Message History (for DMs)
#    - View Channels (to detect joins)

DATABASE_FILE = 'nickname_roles.json'

# --- Bot Setup ---
# Define necessary intents
intents = discord.Intents.default()
intents.members = True  # Required for on_member_join
intents.message_content = True # Required to read DM replies
intents.messages = True # Required for DMs

# Bot instance
bot = commands.Bot(command_prefix="!", intents=intents) # Prefix is not used for slash commands but required

# --- Database Handling ---
# Structure: { "nickname": [role_id1, role_id2, ...], ... }
nickname_role_db: Dict[str, List[int]] = {}
pending_verifications: Dict[int, int] = {} # {user_id: guild_id}

def load_database():
    """Loads the nickname-role database from the JSON file."""
    global nickname_role_db
    try:
        if os.path.exists(DATABASE_FILE):
            with open(DATABASE_FILE, 'r') as f:
                nickname_role_db = json.load(f)
                # Ensure values are lists of integers
                for nickname, roles in nickname_role_db.items():
                    if isinstance(roles, list):
                        nickname_role_db[nickname] = [int(r) for r in roles if isinstance(r, (int, str)) and str(r).isdigit()]
                    else: # Handle potential old format or corruption
                         nickname_role_db[nickname] = []
                print("Nickname database loaded successfully.")
        else:
            nickname_role_db = {}
            print("No existing database file found. Starting fresh.")
    except (json.JSONDecodeError, IOError) as e:
        print(f"Error loading database: {e}. Starting with an empty database.")
        nickname_role_db = {}

def save_database():
    """Saves the current nickname-role database to the JSON file."""
    try:
        with open(DATABASE_FILE, 'w') as f:
            json.dump(nickname_role_db, f, indent=4)
        # print("Nickname database saved.") # Optional: Can be noisy
    except IOError as e:
        print(f"Error saving database: {e}")

# --- Bot Events ---

@bot.event
async def on_ready():
    """Event triggered when the bot is ready and connected."""
    print(f'Logged in as {bot.user.name} ({bot.user.id})')
    print('------')
    load_database()
    try:
        # Sync slash commands globally. Can take up to an hour to propagate.
        # For faster testing, sync to a specific guild:
        # await bot.tree.sync(guild=discord.Object(id=YOUR_GUILD_ID))
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} command(s)")
    except Exception as e:
        print(f"Error syncing commands: {e}")

@bot.event
async def on_member_join(member: discord.Member):
    """Event triggered when a new member joins a server."""
    print(f'{member.name} ({member.id}) joined {member.guild.name}')

    # Don't try to DM bots
    if member.bot:
        return

    # Store user ID and guild ID for verification process
    pending_verifications[member.id] = member.guild.id

    try:
        dm_channel = await member.create_dm()
        welcome_message = (
            f"Welcome to **{member.guild.name}**, {member.mention}!\n\n"
            "To gain access and get your role(s), please reply to this message "
            "with your exact in-game nickname."
        )
        await dm_channel.send(welcome_message)
        print(f"Sent welcome DM to {member.name}")

    except discord.Forbidden:
        print(f"Could not send DM to {member.name}. They might have DMs disabled.")
        # Optionally send a message in a public channel tagging the user,
        # but be mindful of privacy and channel clutter.
    except Exception as e:
        print(f"An error occurred sending DM to {member.name}: {e}")
    finally:
        # Optional: Add a timeout for verification
        # await asyncio.sleep(3600) # e.g., 1 hour
        # if member.id in pending_verifications:
        #     del pending_verifications[member.id]
        #     print(f"Removed {member.name} from pending verifications due to timeout.")
        pass


@bot.event
async def on_message(message: discord.Message):
    """Event triggered when a message is sent that the bot can see."""
    # Ignore messages from bots (including itself)
    if message.author.bot:
        return

    # Check if the message is a DM and from a user pending verification
    if isinstance(message.channel, discord.DMChannel) and message.author.id in pending_verifications:
        user_id = message.author.id
        guild_id = pending_verifications.get(user_id)

        if not guild_id: # Should not happen if logic is correct, but good to check
             print(f"Error: User {user_id} in DM but not found in pending_verifications.")
             return

        guild = bot.get_guild(guild_id)
        if not guild:
            print(f"Error: Could not find guild with ID {guild_id} for user {user_id}.")
            # Clean up if guild is gone or bot was removed
            del pending_verifications[user_id]
            return

        member = guild.get_member(user_id)
        if not member:
            print(f"Error: Could not find member with ID {user_id} in guild {guild.name}.")
            # Clean up if member left
            del pending_verifications[user_id]
            return

        # Treat the entire message content as the nickname
        nickname_attempt = message.content.strip()
        print(f"Received nickname attempt '{nickname_attempt}' from {message.author.name} ({user_id}) for guild {guild.name}")

        # --- Nickname Lookup and Role Assignment ---
        assigned_roles_names = []
        roles_to_assign_ids = nickname_role_db.get(nickname_attempt)

        if roles_to_assign_ids:
            roles_to_add_objects: List[discord.Role] = []
            not_found_role_ids = []

            for role_id in roles_to_assign_ids:
                role = guild.get_role(role_id)
                if role:
                    # Check if bot has permission to assign this role (hierarchy)
                    if guild.me.top_role > role:
                         roles_to_add_objects.append(role)
                    else:
                        print(f"Permission Error: Bot role is lower than role '{role.name}' ({role.id}) in guild {guild.name}. Cannot assign.")
                        await message.channel.send(f"I don't have permission to assign the role '{role.name}'. Please contact an administrator.")
                        # Decide if you want to stop the whole process or just skip this role
                        # return # Uncomment to stop if any role is too high
                else:
                    print(f"Warning: Role ID {role_id} (for nickname '{nickname_attempt}') not found in guild {guild.name}. It might have been deleted.")
                    not_found_role_ids.append(str(role_id)) # Keep track to inform admin maybe

            if roles_to_add_objects:
                try:
                    await member.add_roles(*roles_to_add_objects, reason=f"Verified via nickname: {nickname_attempt}")
                    assigned_roles_names = [r.name for r in roles_to_add_objects]
                    print(f"Successfully assigned roles: {', '.join(assigned_roles_names)} to {member.name} in {guild.name}")
                    await message.channel.send(
                        f"Verification successful! You've been assigned the following role(s): **{', '.join(assigned_roles_names)}**."
                    )
                    if not_found_role_ids:
                         await message.channel.send(
                             f"(Note: Some role IDs associated with this nickname ({', '.join(not_found_role_ids)}) were not found on the server and could not be assigned.)"
                         )

                except discord.Forbidden:
                    print(f"Permission Error: Bot lacks 'Manage Roles' permission in {guild.name}.")
                    await message.channel.send("I don't have the necessary 'Manage Roles' permission to assign roles. Please contact an administrator.")
                except discord.HTTPException as e:
                    print(f"HTTP Error assigning roles to {member.name} in {guild.name}: {e}")
                    await message.channel.send("An unexpected error occurred while assigning roles. Please try again later or contact an administrator.")
                except Exception as e:
                    print(f"Unexpected error during role assignment for {member.name}: {e}")
                    await message.channel.send("An unexpected error occurred. Please contact an administrator.")
            elif not assigned_roles_names and not not_found_role_ids:
                 # This case happens if all roles were higher than the bot's role
                 print(f"No roles could be assigned to {member.name} due to hierarchy issues.")
                 # Message already sent inside the loop

        else:
            # Nickname not found in database
            print(f"Nickname '{nickname_attempt}' not found in database for user {message.author.name}.")
            await message.channel.send(
                "Sorry, that nickname wasn't recognized in our database. \n"
                "Please double-check the spelling and capitalization and try again. "
                "If you're sure it's correct, contact an administrator for assistance."
            )

        # Verification attempt complete, remove user from pending list
        if user_id in pending_verifications:
            del pending_verifications[user_id]
            print(f"Removed {message.author.name} from pending verifications.")

    # IMPORTANT: Allow processing of other commands (if you add any non-slash commands)
    # await bot.process_commands(message) # Only needed if using prefix commands too

# --- Admin Slash Command ---

@bot.tree.command(name="addnickname", description="Link an in-game nickname to a server role for verification.")
@app_commands.describe(
    nickname="The exact in-game nickname to add.",
    role="The role to assign when this nickname is verified."
)
@app_commands.checks.has_permissions(administrator=True) # Only admins can use this
async def add_nickname(interaction: discord.Interaction, nickname: str, role: discord.Role):
    """Adds or updates a nickname-role link in the database."""
    nickname_clean = nickname.strip() # Remove leading/trailing whitespace
    role_id = role.id

    if not nickname_clean:
        await interaction.response.send_message("Nickname cannot be empty.", ephemeral=True)
        return

    # Add/Update the database
    if nickname_clean in nickname_role_db:
        # Nickname exists, add role ID if not already present
        if role_id not in nickname_role_db[nickname_clean]:
            nickname_role_db[nickname_clean].append(role_id)
            action = "updated"
        else:
            await interaction.response.send_message(
                f"Nickname '{nickname_clean}' is already linked to the role '{role.name}'. No changes made.",
                ephemeral=True
            )
            return # No change needed, exit early
    else:
        # New nickname, create entry
        nickname_role_db[nickname_clean] = [role_id]
        action = "added"

    save_database() # Save changes to the file

    print(f"Admin {interaction.user.name} {action} nickname '{nickname_clean}' -> role '{role.name}' ({role_id})")
    await interaction.response.send_message(
        f"Successfully {action} link: Nickname `'{nickname_clean}'` is now linked to role **{role.name}**.",
        ephemeral=True # Only visible to the admin who used the command
    )

@add_nickname.error
async def add_nickname_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    """Error handler for the addnickname command."""
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message("Sorry, you need Administrator permissions to use this command.", ephemeral=True)
    elif isinstance(error, app_commands.CommandInvokeError) and isinstance(error.original, discord.NotFound):
         # This might happen if the role provided doesn't exist (though discord.Role type hint usually prevents this)
         await interaction.response.send_message("Error: The specified role could not be found.", ephemeral=True)
    else:
        print(f"Error in /addnickname command: {error}")
        await interaction.response.send_message("An unexpected error occurred. Please check the bot's console.", ephemeral=True)


# --- Run the Bot ---
if __name__ == "__main__":
    if BOT_TOKEN == 'YOUR_BOT_TOKEN':
        print("ERROR: Please replace 'YOUR_BOT_TOKEN' with your actual bot token in the script.")
    else:
        load_database() # Load DB before running
        bot.run(BOT_TOKEN)

