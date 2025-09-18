import discord
from discord.ext import commands
import asyncio
import re
import requests
import json
import datetime

# --- 1. Bot Configuration ---

intents = discord.Intents.default()
intents.message_content = True
intents.reactions = True

bot = commands.Bot(command_prefix='!', intents=intents)

MOVIE_ROLE_ID = 1418056361446473859
PERMITTED_CHANNEL_ID = 1418107256997806230
OMDB_API_KEY = '9ef031f7'

# File names for persistent data
RATED_MOVIES_DB_FILE = 'rated_movies_db.json'
RATED_USERS_DB_FILE = 'rated_users_db.json'

rated_movies = {}
rated_users_db = {}

# --- 2. Helper Functions and Checks ---

def is_in_specific_channel(ctx):
    """Verifies if the command is used in the permitted channel."""
    return ctx.channel.id == PERMITTED_CHANNEL_ID

def load_rated_movies():
    """Loads rated movies from the JSON file."""
    global rated_movies
    try:
        with open(RATED_MOVIES_DB_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
            rated_movies = {k: int(v) for k, v in data.items()}
    except (FileNotFoundError, json.JSONDecodeError):
        rated_movies = {}

def save_rated_movies():
    """Saves rated movies to the JSON file."""
    try:
        with open(RATED_MOVIES_DB_FILE, 'w', encoding='utf-8') as f:
            json.dump(rated_movies, f, indent=4)
    except Exception as e:
        print(f"Error al guardar la base de datos de películas calificadas: {e}")

def load_rated_users():
    """Loads rated users from the JSON file."""
    global rated_users_db
    try:
        with open(RATED_USERS_DB_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
            rated_users_db = {int(k): set(v) for k, v in data.items()}
    except (FileNotFoundError, json.JSONDecodeError):
        rated_users_db = {}

def save_rated_users():
    """Saves rated users to the JSON file."""
    try:
        with open(RATED_USERS_DB_FILE, 'w', encoding='utf-8') as f:
            data = {str(k): list(v) for k, v in rated_users_db.items()}
            json.dump(data, f, indent=4)
    except Exception as e:
        print(f"Error al guardar la base de datos de usuarios calificados: {e}")

async def update_average_rating(channel):
    """Calculates and updates the average rating in the main message."""
    if not isinstance(channel, discord.Thread):
        return

    ratings = []
    try:
        main_channel = channel.parent
        async for msg in main_channel.history(limit=50):
            if msg.thread and msg.thread.id == channel.id:
                original_message = msg
                break
        else:
            return

    except (discord.NotFound, discord.HTTPException):
        return

    # Iterate through all messages in the thread to find reviews
    async for message in channel.history(limit=100):
        # Check for review messages sent by the bot after a modal submission
        if message.author == bot.user and message.content:
            match = re.search(r'\*\*Calificación:\*\* ⭐+\s\((\d)/5\)', message.content)
            if match:
                rating = int(match.group(1))
                if 1 <= rating <= 5:
                    ratings.append(rating)
    
    embed = original_message.embeds[0]
    if ratings:
        average_rating = sum(ratings) / len(ratings)
        stars = '⭐' * int(round(average_rating))
        
        embed.set_field_at(0, name="Calificación", value=f"{stars} ({average_rating:.2f}/5)", inline=False)
        embed.description = f"Hasta ahora el rating de esta película es: {average_rating:.2f}/5\n¡Vota y deja tu reseña haciendo clic en los botones de abajo!"
    else:
        embed.set_field_at(0, name="Calificación", value="Sin calificar aún", inline=False)
        embed.description = "¡Sé el primero en calificar esta película! Haz clic en los botones de abajo para votar y dejar tu reseña."

    await original_message.edit(embed=embed)


async def send_movie_promotion(guild, thread_url, movie_name):
    """Sends a DM to all members with the movie role."""
    movie_role = guild.get_role(MOVIE_ROLE_ID)
    if not movie_role:
        print("Error: No se pudo encontrar el rol de película. Verifica el ID.")
        return

    for member in movie_role.members:
        if member.bot:
            continue
        try:
            await member.send(f"Hola, hemos visto que tienes el rol de **{movie_role.name}**.\n\n"
                                 f"¿Ya viste y calificaste **'{movie_name}'**?\n\n"
                                 f"¡Es tu momento de dejar tu reseña! Puedes hacerlo directamente aquí: {thread_url}")
            await asyncio.sleep(1)
        except (discord.Forbidden, Exception):
            pass

async def create_movie_review_thread(channel, author, movie_data):
    """Creates a review thread with movie details."""
    movie_title = movie_data.get('Title')
    poster_url = movie_data.get('Poster')
    imdb_id = movie_data.get('imdbID')

    if poster_url == 'N/A':
        poster_url = None

    embed = discord.Embed(
        title=f"🎬 Reseña para '{movie_title}'",
        description="¡Haz clic en el hilo de abajo para calificar y dejar tu reseña!",
        color=discord.Color.gold()
    )
    embed.add_field(name="Calificación", value="Sin calificar aún", inline=False)

    if poster_url:
        embed.set_image(url=poster_url)
    
    message = await channel.send(embed=embed)
    thread_name = f"Reseñas para {movie_title[:20]}..." if len(movie_title) > 20 else f"Reseñas para {movie_title}"
    
    try:
        thread = await message.create_thread(name=thread_name, auto_archive_duration=60, reason="Hilo de reseñas de películas")
        buttons_message = await thread.send("Por favor, usa los botones para calificar esta película:", view=MovieReviewView())
        
        if imdb_id:
            rated_movies[imdb_id.lower().strip()] = thread.id
            save_rated_movies()
            await send_movie_promotion(channel.guild, thread.jump_url, movie_title)
            
    except discord.Forbidden:
        await channel.send("Error de permisos: No puedo crear hilos o configurarlos.", delete_after=30)
    except Exception as e:
        await channel.send(f"Ocurrió un error inesperado al configurar el hilo: {e}.", delete_after=30)

# --- 3. Discord Views (Interactive Components) ---

class MovieReviewView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        
    @discord.ui.button(label='1 ⭐', style=discord.ButtonStyle.red, custom_id='review_1')
    async def review_1(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.handle_review(interaction, 1)

    @discord.ui.button(label='2 ⭐', style=discord.ButtonStyle.red, custom_id='review_2')
    async def review_2(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.handle_review(interaction, 2)

    @discord.ui.button(label='3 ⭐', style=discord.ButtonStyle.gray, custom_id='review_3')
    async def review_3(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.handle_review(interaction, 3)

    @discord.ui.button(label='4 ⭐', style=discord.ButtonStyle.green, custom_id='review_4')
    async def review_4(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.handle_review(interaction, 4)

    @discord.ui.button(label='5 ⭐', style=discord.ButtonStyle.green, custom_id='review_5')
    async def review_5(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.handle_review(interaction, 5)

    async def handle_review(self, interaction: discord.Interaction, rating: int):
        user_id = interaction.user.id
        thread_id = interaction.channel.id

        if user_id in rated_users_db.get(thread_id, set()):
            try:
                # Intenta enviar una respuesta efímera para notificar al usuario
                await interaction.response.send_message("❌ Ya has dejado una reseña en este hilo. Solo se permite una por usuario.", ephemeral=True, delete_after=5)
            except discord.errors.InteractionResponded:
                # Ignora el error si la interacción ya ha sido respondida, lo que evita el traceback
                pass
            return
            
        # El bot no necesita deshabilitar los botones, ya que cada interacción se gestiona individualmente.
        try:
            await interaction.response.send_modal(MovieReviewModal(rating))
        except discord.errors.InteractionResponded:
            # Captura y maneja el error si la interacción ya fue respondida, lo que podría pasar
            # si el usuario presiona los botones muy rápido.
            pass


class MovieReviewModal(discord.ui.Modal):
    def __init__(self, rating):
        super().__init__(title=f"Tu Reseña ({rating}⭐)")
        self.rating = rating
        self.review_text = discord.ui.TextInput(
            label="Escribe tu reseña",
            style=discord.TextStyle.long,
            placeholder="¡Qué gran película!",
            required=True,
            max_length=500
        )
        self.add_item(self.review_text)

    async def on_submit(self, interaction: discord.Interaction):
        thread_id = interaction.channel.id
        user_id = interaction.user.id
        
        rated_users_db.setdefault(thread_id, set()).add(user_id)
        save_rated_users()
        
        await interaction.response.defer(ephemeral=True)
        review_description = self.review_text.value
        stars = '⭐' * self.rating
        review_message_content = (
            f"**Reseña de {interaction.user.display_name}:**\n"
            f"{review_description}\n"
            f"**Calificación:** {stars} ({self.rating}/5)"
        )
        try:
            await interaction.channel.send(content=review_message_content)
        except (discord.Forbidden, Exception):
            pass
        await update_average_rating(interaction.channel)

# --- 4. Event Handlers ---

@bot.event
async def on_ready():
    """Runs when the bot connects to Discord."""
    print(f'¡El bot {bot.user} está listo y funcionando!')
    load_rated_movies()
    load_rated_users()
    bot.add_view(MovieReviewView())

@bot.event
async def on_message(message):
    """Monitors messages to handle commands and auto-deletion."""
    if message.author.bot:
        return

    # Delete any user message in a review thread
    if isinstance(message.channel, discord.Thread):
        try:
            await message.delete()
        except (discord.NotFound, discord.Forbidden):
            pass
        return

    # Delete non-command messages in the permitted channel
    if message.channel.id == PERMITTED_CHANNEL_ID:
        is_command = message.content.startswith(('!rate', '!buscar', '!lista'))
        
        if not is_command:
            try:
                await message.delete()
                await message.channel.send(f"❌ Para interactuar en este canal, por favor usa los comandos permitidos (`!rate`, `!buscar`, `!lista`).", delete_after=10)
            except (discord.errors.NotFound, discord.Forbidden):
                pass
        
    await bot.process_commands(message)
    
@bot.event
async def on_reaction_add(reaction, user):
    """Removes reactions from permitted channels and threads."""
    if user == bot.user:
        return

    if reaction.message.channel.id == PERMITTED_CHANNEL_ID or isinstance(reaction.message.channel, discord.Thread):
        try:
            await reaction.message.remove_reaction(reaction.emoji, user)
        except discord.errors.Forbidden:
            print("Error: No tengo permisos para eliminar reacciones.")
        except Exception as e:
            print(f"Ocurrió un error inesperado al intentar eliminar una reacción: {e}")

@bot.event
async def on_command_error(ctx, error):
    """Handles command-related errors."""
    try:
        await ctx.message.delete()
    except (discord.errors.NotFound, discord.Forbidden):
        pass

    if isinstance(error, commands.CheckFailure):
        permitted_channel = bot.get_channel(PERMITTED_CHANNEL_ID)
        if permitted_channel:
            channel_name = permitted_channel.name
            await ctx.send(f"❌ Lo siento, este comando solo se puede usar en el canal **#{channel_name}**.", delete_after=10)
        else:
            await ctx.send("❌ Lo siento, este comando solo se puede usar en un canal designado.", delete_after=10)
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(f"❌ Faltan argumentos. Uso: `!{ctx.command.name} \"<título de la película>\"`", delete_after=10)
    elif isinstance(error, commands.MissingRole):
        await ctx.send("❌ Lo siento, no tienes el rol necesario para usar este comando.", delete_after=10)
    elif isinstance(error, commands.CommandNotFound):
        await ctx.send("❌ Ese comando no existe. Revisa la lista de comandos disponibles.", delete_after=10)
    else:
        print(f"Error inesperado: {error}")

# --- 5. Commands ---

@bot.command(name='rate')
@commands.has_role(MOVIE_ROLE_ID)
@commands.check(is_in_specific_channel)
async def rate_movie(ctx, *, title: str):
    try:
        await ctx.message.delete()
    except discord.errors.NotFound:
        pass
    
    # Check for direct IMDb ID input first
    if re.match(r'^tt\d{7,8}$', title.lower()) is not None:
        imdb_id = title.lower()
        if imdb_id in rated_movies:
            thread = ctx.guild.get_thread(rated_movies[imdb_id])
            if thread:
                await ctx.send(f"❌ La película **'{imdb_id}'** ya ha sido calificada. Ver reseñas aquí: {thread.jump_url}", delete_after=10)
                return
        
        try:
            movie_data = requests.get(f'http://www.omdbapi.com/?i={imdb_id}&apikey={OMDB_API_KEY}').json()
            if movie_data.get('Response') == 'True':
                await create_movie_review_thread(ctx.channel, ctx.author, movie_data)
            else:
                await ctx.send(f"❌ No se encontró una película con el ID de IMDb **'{imdb_id}'**.", delete_after=10)
        except requests.exceptions.RequestException:
            await ctx.send("❌ Ocurrió un error al buscar la película por ID.", delete_after=10)
        return

    # If not a direct ID, perform a search
    all_movies = []
    page = 1
    while len(all_movies) < 50: # Limit search to 5 pages
        try:
            search_data = requests.get(f'http://www.omdbapi.com/?s={title}&type=movie&apikey={OMDB_API_KEY}&page={page}').json()
        except requests.exceptions.RequestException:
            await ctx.send("❌ Ocurrió un error con la API de películas.", delete_after=10)
            return
        
        if search_data.get('Response') == 'False':
            break
        
        for movie in search_data.get('Search', []):
            if movie.get('imdbID') and movie.get('imdbID') not in [m.get('imdbID') for m in all_movies]:
                all_movies.append(movie)

        if len(all_movies) >= int(search_data.get('totalResults', 0)):
            break
        page += 1
        await asyncio.sleep(0.5)

    if not all_movies:
        await ctx.send(f"❌ Lo siento, no pude encontrar ninguna película con el título **'{title}'**.", delete_after=10)
        return

    # Process and filter the movies
    def get_year_for_sort(movie):
        year_str = movie.get('Year', '0')
        match = re.search(r'\d{4}', year_str)
        return int(match.group(0)) if match else 0
        
    all_movies.sort(key=get_year_for_sort, reverse=True)

    valid_movies = []
    already_rated_links = []
    
    for movie in all_movies:
        imdb_id = movie.get('imdbID')
        if imdb_id and imdb_id.lower().strip() in rated_movies:
            thread = ctx.guild.get_thread(rated_movies[imdb_id.lower().strip()])
            if thread:
                already_rated_links.append(f"**'{movie.get('Title')} ({movie.get('Year')})'** ([Ver reseñas]({thread.jump_url}))")
        elif len(valid_movies) < 20: # Limit the main list to 20 movies
            valid_movies.append(movie)

    if not valid_movies and not already_rated_links:
        await ctx.send(f"❌ No se encontraron resultados válidos (con IMDb ID) para **'{title}'**.", delete_after=10)
        return
        
    # Build and send the single, unified message
    lines = [f"Resultados para **'{title}'**. Responde con el número para calificar:"]

    if valid_movies:
        lines.append("\n".join([f"**{i + 1}.** {m.get('Title')} ({m.get('Year')})" for i, m in enumerate(valid_movies)]))
    else:
        lines.append("❌ No se encontraron películas sin calificar.")
    
    lines.append("\n---")
    
    if already_rated_links:
        lines.append("\n**Películas ya calificadas:**")
        lines.append("\n".join(already_rated_links))

    lines.append("\nSi tu película no está aquí, busca con un título más específico o introduce el ID de IMDb directamente.")
    
    message_to_delete = await ctx.send("\n".join(lines))

    def check(m): 
        is_valid_id = re.match(r'^tt\d{7,8}$', m.content.lower()) is not None
        return m.author == ctx.author and m.channel == ctx.channel and (m.content.isdigit() or is_valid_id)
    
    response_message = None
    try:
        response_message = await bot.wait_for('message', check=check, timeout=60.0)
        user_input = response_message.content.lower()

        if re.match(r'^tt\d{7,8}$', user_input) is not None:
            imdb_id = user_input
            if imdb_id in rated_movies:
                thread = ctx.guild.get_thread(rated_movies[imdb_id])
                if thread:
                    await ctx.send(f"❌ La película **'{imdb_id}'** ya ha sido calificada. Ver reseñas aquí: {thread.jump_url}", delete_after=10)
                    return
            
            try:
                movie_data = requests.get(f'http://www.omdbapi.com/?i={imdb_id}&apikey={OMDB_API_KEY}').json()
                if movie_data.get('Response') == 'True':
                    await create_movie_review_thread(ctx.channel, ctx.author, movie_data)
                else:
                    await ctx.send(f"❌ No se encontró una película con el ID de IMDb **'{imdb_id}'**.", delete_after=10)
            except requests.exceptions.RequestException:
                await ctx.send("❌ Ocurrió un error al buscar la película por ID.", delete_after=10)
        else:
            selected_index = int(user_input) - 1
            if 0 <= selected_index < len(valid_movies):
                selected_movie_id = valid_movies[selected_index].get('imdbID')
                selected_movie_data = requests.get(f'http://www.omdbapi.com/?i={selected_movie_id}&apikey={OMDB_API_KEY}').json()
                await create_movie_review_thread(ctx.channel, ctx.author, selected_movie_data)
            else:
                await ctx.send("❌ Selección inválida. Ingresa un número de la lista o un ID de IMDb.", delete_after=10)
            
    except (asyncio.TimeoutError, ValueError):
        await ctx.send("⌛ Tiempo agotado o respuesta inválida. Usa `!rate` de nuevo.", delete_after=10)
    finally:
        if message_to_delete: 
            try: await message_to_delete.delete()
            except discord.errors.NotFound: pass
        if response_message:
            try: await response_message.delete()
            except discord.errors.NotFound: pass

@bot.command(name='buscar')
@commands.check(is_in_specific_channel)
async def find_movie(ctx, *, title: str):
    try: await ctx.message.delete()
    except discord.NotFound: pass

    try:
        search_data = requests.get(f'http://www.omdbapi.com/?s={title}&type=movie&apikey={OMDB_API_KEY}').json()
    except requests.exceptions.RequestException:
        await ctx.send("Error al conectar con la API de películas.", delete_after=10)
        return

    if search_data.get('Response') == 'True' and search_data.get('Search'):
        response_message = f"Resultados para **'{title}'**:\n"
        
        for movie in search_data.get('Search', []):
            imdb_id = movie.get('imdbID')
            if imdb_id and imdb_id.lower().strip() in rated_movies:
                thread = ctx.guild.get_thread(rated_movies[imdb_id.lower().strip()])
                if thread:
                    response_message += f" **'{movie.get('Title')} ({movie.get('Year')})'** ya ha sido calificada. Ver reseñas: {thread.jump_url}\n"
            else:
                response_message += f" **'{movie.get('Title')} ({movie.get('Year')})'** aún no tiene reseñas. Usa `!rate` para calificarla.\n"
        
        await ctx.send(response_message, delete_after=30)
    else:
        await ctx.send(f"La película **'{title}'** no ha sido calificada o no se encontró.", delete_after=10)
    
@bot.command(name='lista')
@commands.check(is_in_specific_channel)
async def list_movies(ctx):
    try: await ctx.message.delete()
    except discord.NotFound: pass
    load_rated_movies()

    if not rated_movies:
        await ctx.send("No hay películas calificadas.", delete_after=10)
        return

    message_content = "**🎥 Películas calificadas:**\n\n"
    
    # Invertir el diccionario para mostrar las últimas películas primero
    reversed_movies = dict(reversed(rated_movies.items()))

    for imdb_id, thread_id in reversed_movies.items():
        thread = ctx.guild.get_thread(thread_id)
        if thread:
            try:
                # Obtener la información de la película de la API de OMDB usando el imdb_id
                movie_data = requests.get(f'http://www.omdbapi.com/?i={imdb_id}&apikey={OMDB_API_KEY}').json()
                if movie_data.get('Response') == 'True':
                    movie_title = movie_data.get('Title')
                    movie_year = movie_data.get('Year')
                    line = f"**-** {movie_title} ({movie_year}) - ([Ver reseñas]({thread.jump_url}))\n"
                else:
                    line = f"- Película (ID: {imdb_id}) ([Ver reseñas]({thread.jump_url}))\n"
            except (requests.exceptions.RequestException, Exception):
                line = f"- Película (ID: {imdb_id}) ([Ver reseñas]({thread.jump_url}))\n"
            
            # Verificar si añadir la siguiente línea superaría el límite de caracteres
            if len(message_content) + len(line) > 2000:
                await ctx.send(message_content)
                message_content = line
            else:
                message_content += line
    
    if message_content:
        await ctx.send(message_content, delete_after=30)

# --- 6. Bot Run ---
import os
bot.run(os.getenv("DISCORD_TOKEN"))