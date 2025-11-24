from flask import Blueprint, render_template, request, redirect, url_for, session, flash
from app.extensions import db
from datetime import datetime, timedelta
from flask_wtf import FlaskForm
from wtforms import StringField, SubmitField, EmailField, SelectField, DateField, DecimalField
from wtforms.validators import DataRequired, Email, ValidationError, NumberRange
import pycountry
from urllib.parse import unquote

def get_country_choices():
    countries = [(country.name, country.name) for country in pycountry.countries]
    countries.sort(key=lambda x: x[0])
    return countries



class PaginationInfo:
    def __init__(self, items, page, pages, total, has_prev, has_next, prev_num, next_num):
        self.items = items
        self.page = page
        self.pages = pages
        self.total = total
        self.has_prev = has_prev
        self.has_next = has_next
        self.prev_num = prev_num
        self.next_num = next_num

class LoginForm(FlaskForm):
    username = StringField('Username:', validators=[DataRequired()])
    submit = SubmitField('Login')

class CreateAccountForm(FlaskForm):
    username = StringField('Username:', validators=[DataRequired()])
    email = EmailField('Email:', validators=[DataRequired(), Email()])
    gender = SelectField('Gender:', choices=[('M', 'Male'), ('F', 'Female'), ('O', 'Prefer not to say')], validators=[DataRequired()])
    country = SelectField('Country:', choices=get_country_choices(), validators=[DataRequired()])
    birthdate = DateField(
        'Date of Birth:',
        format='%Y-%m-%d',
        validators=[DataRequired(message='Please enter a valid date')]
    )
    submit = SubmitField('Sign Up')

    def validate_birthdate(self, field):
        if field.data:
            three_years_ago = datetime.now().date() - timedelta(days=3*365)

            if field.data > three_years_ago:
                raise ValidationError('You must be at least 3 years old to create an account.')

class RateForm(FlaskForm):
    platform = SelectField('Your used platform', validators=[DataRequired(message='Please select a platform')])
    rating = DecimalField('Your Rating Out Of 5', places=1,
        validators=[DataRequired(), NumberRange(min=0, max=5, message='Rating must be between 0 and 5')])
    submit = SubmitField('Rate')

    def validate_rating(self, field):
        if field.data is not None:
            rating_str = str(field.data)
            if '.' in rating_str:
                decimals = len(rating_str.split('.')[1])
                if decimals > 1:
                    raise ValidationError('Rating must have a maximum of 1 decimal place (e.g., 3.5)')


main_blueprint = Blueprint('main', __name__)
@main_blueprint.route('/create_account', methods=['GET', 'POST'])
def create_account():
    create_form = CreateAccountForm()
    if create_form.validate_on_submit():
        sql = "SELECT Username, Email FROM `User` WHERE Username = :username OR Email = :email LIMIT 1"
        result = db.session.execute(db.text(sql), {'username': create_form.username.data, 'email': create_form.email.data}).first()
        if result:
            flash('Username or email already exists', 'error')
            return redirect(url_for('main.create_account'))
        gender = create_form.gender.data
        if gender == 'O':
            gender = None

        insert_sql = "INSERT INTO `User` (Username, Gender, Email, Country, DOB) VALUES (:username, :gender, :email, :country, :dob)"
        db.session.execute(db.text(insert_sql), {
            'username': create_form.username.data,
            'gender': gender if gender else None,
            'email': create_form.email.data,
            'country': create_form.country.data,
            'dob': create_form.birthdate.data
        })
        db.session.commit()

        flash('Account created successfully! Please login.', 'success')
        return redirect(url_for('main.login'))

    return render_template('create_account.html', form=create_form)


@main_blueprint.route('/login', methods=['GET', 'POST'])
def login():
    login_form = LoginForm()
    if login_form.validate_on_submit():
        sql = "SELECT Username FROM `User` WHERE Username = :username LIMIT 1"
        result = db.session.execute(db.text(sql), {'username': login_form.username.data}).first()
        if result:
            session['username'] = login_form.username.data
            flash(f'Welcome back, {login_form.username.data}!', 'success')
            return redirect(url_for('main.games'))
        else:
            flash('User not found', 'error')
            return redirect(url_for('main.login'))

    return render_template('login.html', form=login_form)


# Home and Main Pages

@main_blueprint.route('/')
def home():
    if 'username' in session:
        return redirect(url_for('main.games'))
    return render_template('home.html')


@main_blueprint.route('/games')
def games():
    if 'username' not in session:
        flash('Please login first', 'warning')
        return redirect(url_for('main.login'))

    page = request.args.get('page', 1, type=int)
    per_page = 20
    offset = (page - 1) * per_page

    order_by = request.args.get('order_by', 'None')
    year = request.args.get('year', 'All')
    genre = request.args.get('genre', 'All')

    where_conditions = []
    having_conditions = []
    params = {}

    if year != 'All':
        having_conditions.append("YEAR(MIN(gp.DateOfRelease)) = :year")
        params['year'] = int(year)

    if genre != 'All':
        where_conditions.append("gg.Genre = :genre")
        params['genre'] = genre

    where_clause = " AND ".join(where_conditions)
    if where_clause:
        where_clause = "WHERE " + where_clause
    else:
        where_clause = ""

    having_clause = " AND ".join(having_conditions)
    if having_clause:
        having_clause = "HAVING " + having_clause
    else:
        having_clause = ""

    if order_by == 'MobyScore':
        order_clause = "ORDER BY g.MobyScore DESC, g.`Name`"
    elif order_by == 'CriticRating':
        order_clause = "ORDER BY avg_critic DESC, g.`Name`"
    elif order_by == 'UserRating':
        order_clause = "ORDER BY avg_user DESC, g.`Name`"
    else:
        order_clause = "ORDER BY g.`Name`"

    count_sql = f"""
        SELECT COUNT(*) AS total
        FROM (
            SELECT DISTINCT g.ID
            FROM Game g
            {('LEFT JOIN GamesPlatform gp ON g.ID = gp.GameID' if (order_by != 'MobyScore' or year != 'All') else '')}
            {('LEFT JOIN GameGenre gg ON g.ID = gg.GameID' if genre != 'All' else '')}
            {where_clause}
            {('GROUP BY g.ID' if year != 'All' or genre != 'All' else '')}
            {having_clause}
        ) AS distinct_games
    """

    total_result = db.session.execute(db.text(count_sql), params).first()
    total_games = total_result.total if total_result else 0

    if order_by != "MobyScore":
        sql = f"""
            SELECT 
                g.ID,
                g.CoverPhoto,
                g.`Name`,
                g.MobyScore,
                AVG(gp.AvgCriticRatingPercentage) as avg_critic,
                SUM(gp.TotalPlayerRating) / NULLIF(SUM(gp.NumPlayersRated), 0) as avg_user
            FROM Game g
            LEFT JOIN GamesPlatform gp ON g.ID = gp.GameID
            {('LEFT JOIN GameGenre gg ON g.ID = gg.GameID' if genre != 'All' else '')}
            {where_clause}
            GROUP BY g.ID, g.CoverPhoto, g.`Name`, g.MobyScore
            {having_clause}
            {order_clause}
            LIMIT :limit OFFSET :offset
        """
    else:
        sql = f"""
            SELECT 
                g.ID,
                g.CoverPhoto,
                g.`Name`,
                g.MobyScore
            FROM Game g
            {('LEFT JOIN GamesPlatform gp ON g.ID = gp.GameID' if year != 'All' else '')}
            {('LEFT JOIN GameGenre gg ON g.ID = gg.GameID' if genre != 'All' else '')}
            {where_clause}
            {('GROUP BY g.ID, g.CoverPhoto, g.`Name`, g.MobyScore' if year != 'All' or genre != 'All' else '')}
            {having_clause}
            {order_clause}
            LIMIT :limit OFFSET :offset
        """

    params['limit'] = per_page
    params['offset'] = offset

    games_result = db.session.execute(db.text(sql), params).fetchall()


    years = [2020, 2021, 2022, 2023, 2024, 2025]


    genres_sql = "SELECT `Name` FROM Genre ORDER BY `Name`"
    genres_result = db.session.execute(db.text(genres_sql)).fetchall()
    genres = [g.Name for g in genres_result]


    total_pages = (total_games + per_page - 1) // per_page
    has_prev = page > 1
    has_next = page < total_pages
    prev_num = page - 1 if has_prev else None
    next_num = page + 1 if has_next else None

    pagination = PaginationInfo(games_result, page, total_pages, total_games, has_prev, has_next, prev_num, next_num)

    return render_template('games.html',
                           games=pagination,
                           years=years,
                           genres=genres,
                           selected_order=order_by,
                           selected_year=year,
                           selected_genre=genre)


@main_blueprint.route('/directors')
def directors():
    if 'username' not in session:
        flash('Please login first', 'warning')
        return redirect(url_for('main.login'))

    page = request.args.get('page', 1, type=int)
    per_page = 20
    offset = (page - 1) * per_page

    count_sql = "SELECT COUNT(*) AS total FROM Director"
    total_result = db.session.execute(db.text(count_sql)).first()
    total_directors = total_result.total if total_result else 0

    sql = """
    SELECT d.ID, d.`Name`, d.ProfilePicture, COUNT(*) AS games_num
    FROM Director d INNER JOIN GameDirectors gd
    ON d.ID = gd.DirectorID
    GROUP BY 1, 2, 3
    LIMIT :limit 
    OFFSET :offset
    """
    games_result = db.session.execute(db.text(sql), {
        'limit': per_page,
        'offset': offset
    }).fetchall()

    total_pages = (total_directors + per_page - 1) // per_page
    has_prev = page > 1
    has_next = page < total_pages
    prev_num = None
    if has_prev:
        prev_num = page - 1
    next_num = None
    if has_next:
        next_num = page + 1

    pagination = PaginationInfo(games_result, page, total_pages, total_directors, has_prev, has_next, prev_num, next_num)

    return render_template('directors.html', directors=pagination)

@main_blueprint.route('/companies')
def companies():
    if 'username' not in session:
        flash('Please login first', 'warning')
        return redirect(url_for('main.login'))

    page = request.args.get('page', 1, type=int)
    per_page = 20
    offset = (page - 1) * per_page

    count_sql = "SELECT COUNT(*) AS total FROM Company"
    total_result = db.session.execute(db.text(count_sql)).first()
    total_companies = total_result.total if total_result else 0

    sql = """
    SELECT c.ID, c.`Name`, c.Logo, COUNT(DISTINCT cdg.GameID) AS developed_games_num, COUNT(DISTINCT cpg.GameID) AS published_games_num
    FROM Company c LEFT JOIN CompanyDevelopGame cdg
    ON c.ID = cdg.CompanyID
    LEFT JOIN CompanyPublishGame cpg
    ON c.ID = cpg.CompanyID
    GROUP BY 1, 2, 3
    LIMIT :limit 
    OFFSET :offset
    """
    companies_result = db.session.execute(db.text(sql), {
        'limit': per_page,
        'offset': offset
    }).fetchall()

    total_pages = (total_companies + per_page - 1) // per_page
    has_prev = page > 1
    has_next = page < total_pages
    prev_num = None
    if has_prev:
        prev_num = page - 1
    next_num = None
    if has_next:
        next_num = page + 1

    pagination = PaginationInfo(companies_result, page, total_pages, total_companies, has_prev, has_next, prev_num, next_num)

    return render_template('companies.html', companies=pagination)

@main_blueprint.route('/platform')
def platforms():
    if 'username' not in session:
        flash('Please login first', 'warning')
        return redirect(url_for('main.login'))

    platforms_sql = "SELECT `Name` FROM Platform"
    platforms_result = db.session.execute(db.text(platforms_sql)).fetchall()
    platforms = [platform.Name for platform in platforms_result]

    return render_template('platforms.html', platforms=platforms)


@main_blueprint.route('/game_genres')
def game_genres():
    if 'username' not in session:
        flash('Please login first', 'warning')
        return redirect(url_for('main.login'))

    genres_sql = "SELECT `Name` FROM Genre"
    genres_result = db.session.execute(db.text(genres_sql)).fetchall()
    genres = [genre.Name for genre in genres_result]

    settings_sql = "SELECT `Name` FROM Setting"
    settings_result = db.session.execute(db.text(settings_sql)).fetchall()
    settings = [setting.Name for setting in settings_result]

    gameplays_sql = "SELECT `Name` FROM Gameplay"
    gameplays_result = db.session.execute(db.text(gameplays_sql)).fetchall()
    gameplays = [gameplay.Name for gameplay in gameplays_result]

    interfaces_sql = "SELECT `Name` FROM Interface"
    interfaces_result = db.session.execute(db.text(interfaces_sql)).fetchall()
    interfaces = [interface.Name for interface in interfaces_result]

    perspectives_sql = "SELECT `Name` FROM Perspective"
    perspectives_result = db.session.execute(db.text(perspectives_sql)).fetchall()
    perspectives = [perspective.Name for perspective in perspectives_result]

    visuals_sql = "SELECT `Name` FROM Visual"
    visuals_result = db.session.execute(db.text(visuals_sql)).fetchall()
    visuals = [visual.Name for visual in visuals_result]

    arts_sql = "SELECT `Name` FROM Art"
    arts_result = db.session.execute(db.text(arts_sql)).fetchall()
    arts = [art.Name for art in arts_result]

    narratives_sql = "SELECT `Name` FROM Narrative"
    narratives_result = db.session.execute(db.text(narratives_sql)).fetchall()
    narratives = [narrative.Name for narrative in narratives_result]

    pacings_sql = "SELECT `Name` FROM Pacing"
    pacings_result = db.session.execute(db.text(pacings_sql)).fetchall()
    pacings = [pacing.Name for pacing in pacings_result]

    return render_template('game_genres.html',
                           genres=genres,
                           settings=settings,
                           gameplays=gameplays,
                           interfaces=interfaces,
                           perspectives=perspectives,
                           visuals=visuals,
                           arts=arts,
                           narratives=narratives,
                           pacings=pacings)

@main_blueprint.route('/platform/<path:platform_name>/games') #Platform name may contain a forward slash
def platform_games(platform_name):
    if 'username' not in session:
        flash('Please login first', 'warning')
        return redirect(url_for('main.login'))

    platform_name = unquote(platform_name)
    page = request.args.get('page', 1, type=int)
    per_page = 20
    offset = (page - 1) * per_page

    verify_sql = "SELECT COUNT(GameID) AS count FROM GamesPlatform WHERE PlatformName = :platform_name"
    verify = db.session.execute(db.text(verify_sql), {'platform_name': platform_name}).first()
    if not verify or verify.count == 0:
        flash('Platform not found', 'error')
        return redirect(url_for('main.platforms'))

    total_games = verify.count

    games_sql = """
        SELECT g.ID, g.`Name`, g.CoverPhoto, g.MobyScore
        FROM Game g
        INNER JOIN GamesPlatform gp ON g.ID = gp.GameID
        WHERE gp.PlatformName = :platform_name
        ORDER BY g.`Name`
        LIMIT :limit OFFSET :offset
    """
    games = db.session.execute(db.text(games_sql), {
        'platform_name': platform_name,
        'limit': per_page,
        'offset': offset
    }).fetchall()



    total_pages = (total_games + per_page - 1) // per_page
    has_prev = page > 1
    has_next = page < total_pages
    prev_num = page - 1 if has_prev else None
    next_num = page + 1 if has_next else None


    pagination = PaginationInfo(games, page, total_pages, total_games, has_prev, has_next, prev_num, next_num)

    return render_template('platform_games.html',
                          platform_name=platform_name,
                          games=pagination)


@main_blueprint.route('/game_genres/<string:genre_type>/<path:name>/games')
def genre_games(genre_type, name):
    if 'username' not in session:
        flash('Please login first', 'warning')
        return redirect(url_for('main.login'))

    name = unquote(name)
    page = request.args.get('page', 1, type=int)
    per_page = 20
    offset = (page - 1) * per_page

    possible_genre_types = ['genre', 'setting', 'gameplay', 'interface', 'perspective', 'visual', 'art', 'narrative',
                            'pacing']
    if genre_type not in possible_genre_types:
        flash('Invalid genre type', 'error')
        return redirect(url_for('main.game_genres'))

    table_name = genre_type.title()

    verify_sql = f"SELECT COUNT(*) AS count FROM {table_name} WHERE `Name` = :name"
    verify = db.session.execute(db.text(verify_sql), {'name': name}).first()

    if not verify or verify.count == 0:
        flash(f'{name} not found', 'error')
        return redirect(url_for('main.game_genres'))

    game_table = "Game" + table_name

    count_sql = f"""
        SELECT COUNT(GameID) as total 
        FROM {game_table}
        WHERE {table_name} = :name
    """
    total_result = db.session.execute(db.text(count_sql), {'name': name}).first()
    total_games = total_result.total if total_result else 0

    games_sql = f"""
        SELECT g.ID, g.`Name`, g.CoverPhoto, g.MobyScore
        FROM Game g
        INNER JOIN {game_table} gt ON g.ID = gt.GameID
        WHERE gt.{table_name} = :name
        ORDER BY g.`Name`
        LIMIT :limit OFFSET :offset
    """
    games = db.session.execute(db.text(games_sql), {
        'name': name,
        'limit': per_page,
        'offset': offset
    }).fetchall()


    total_pages = (total_games + per_page - 1) // per_page
    has_prev = page > 1
    has_next = page < total_pages
    prev_num = page - 1 if has_prev else None
    next_num = page + 1 if has_next else None

    pagination = PaginationInfo(games, page, total_pages, total_games, has_prev, has_next, prev_num, next_num)

    return render_template('genre_games.html',
                           genre_type=genre_type,
                           genre_name=name,
                           games=pagination)

# Individual Entity Pages

@main_blueprint.route('/game/<int:game_id>')
def game_detail(game_id):
    if 'username' not in session:
        flash('Please login first', 'warning')
        return redirect(url_for('main.login'))

    game_sql = "SELECT ID, `Name`, Site, MobyScore, CoverPhoto, `Description` FROM Game WHERE ID = :game_id LIMIT 1"
    game = db.session.execute(db.text(game_sql), {'game_id': game_id}).first()

    if not game:
        flash('Game not found', 'error')
        return redirect(url_for('main.games'))

    arts_sql = "SELECT Art FROM GameArt WHERE GameID = :game_id"
    arts = db.session.execute(db.text(arts_sql), {'game_id': game_id}).fetchall()
    arts = [art.Art for art in arts]

    gameplay_sql = "SELECT Gameplay FROM GameGameplay WHERE GameID = :game_id"
    gameplays = db.session.execute(db.text(gameplay_sql), {'game_id': game_id}).fetchall()
    gameplays = [g.Gameplay for g in gameplays]

    narrative_sql = "SELECT Narrative FROM GameNarrative WHERE GameID = :game_id"
    narratives = db.session.execute(db.text(narrative_sql), {'game_id': game_id}).fetchall()
    narratives = [n.Narrative for n in narratives]

    visual_sql = "SELECT Visual FROM GameVisual WHERE GameID = :game_id"
    visuals = db.session.execute(db.text(visual_sql), {'game_id': game_id}).fetchall()
    visuals = [v.Visual for v in visuals]

    perspective_sql = "SELECT Perspective FROM GamePerspective WHERE GameID = :game_id"
    perspectives = db.session.execute(db.text(perspective_sql), {'game_id': game_id}).fetchall()
    perspectives = [p.Perspective for p in perspectives]

    genre_sql = "SELECT Genre FROM GameGenre WHERE GameID = :game_id"
    genres = db.session.execute(db.text(genre_sql), {'game_id': game_id}).fetchall()
    genres = [g.Genre for g in genres]

    interface_sql = "SELECT Interface FROM GameInterface WHERE GameID = :game_id"
    interfaces = db.session.execute(db.text(interface_sql), {'game_id': game_id}).fetchall()
    interfaces = [i.Interface for i in interfaces]

    pacing_sql = "SELECT Pacing FROM GamePacing WHERE GameID = :game_id"
    pacings = db.session.execute(db.text(pacing_sql), {'game_id': game_id}).fetchall()
    pacings = [p.Pacing for p in pacings]

    setting_sql = "SELECT Setting FROM GameSetting WHERE GameID = :game_id"
    settings = db.session.execute(db.text(setting_sql), {'game_id': game_id}).fetchall()
    settings = [s.Setting for s in settings]

    release_sql = "SELECT MIN(DateOfRelease) as FirstRelease FROM GamesPlatform WHERE GameID = :game_id AND DateOfRelease IS NOT NULL"
    first_release = db.session.execute(db.text(release_sql), {'game_id': game_id}).first()
    first_release_date = first_release.FirstRelease if first_release and first_release.FirstRelease else None

    dev_sql = """
        SELECT c.ID, c.`Name`, c.Logo
        FROM Company c
        INNER JOIN CompanyDevelopGame cdg ON c.ID = cdg.CompanyID
        WHERE cdg.GameID = :game_id
    """
    developers = db.session.execute(db.text(dev_sql), {'game_id': game_id}).fetchall()
    developers = [{'id': d.ID, 'name': d.Name, 'logo': d.Logo} for d in developers]

    pub_sql = """
        SELECT c.ID, c.`Name`, c.Logo
        FROM Company c
        INNER JOIN CompanyPublishGame cpg ON c.ID = cpg.CompanyID
        WHERE cpg.GameID = :game_id
    """
    publishers = db.session.execute(db.text(pub_sql), {'game_id': game_id}).fetchall()
    publishers = [{'id': p.ID, 'name': p.Name, 'logo': p.Logo} for p in publishers]

    user_rating_sql = "SELECT Rating, PlatformName FROM UserRatings WHERE Username = :username AND GameID = :game_id LIMIT 1"
    user_rating = db.session.execute(db.text(user_rating_sql), {
        'username': session.get('username'),
        'game_id': game_id
    }).first()
    platform = user_rating.PlatformName if user_rating else None
    user_rating = user_rating.Rating if user_rating else None

    avg_critic_sql = "SELECT AVG(AvgCriticRatingPercentage) as AvgCritic FROM GamesPlatform WHERE GameID = :game_id AND AvgCriticRatingPercentage IS NOT NULL"
    avg_critic = db.session.execute(db.text(avg_critic_sql), {'game_id': game_id}).first()
    avg_critic_rating = round(avg_critic.AvgCritic, 1) if avg_critic and avg_critic.AvgCritic else None

    avg_user_sql = """
        SELECT 
            SUM(TotalPlayerRating) as TotalRating,
            SUM(NumPlayersRated) as TotalPlayers
        FROM GamesPlatform
        WHERE GameID = :game_id
    """
    avg_user = db.session.execute(db.text(avg_user_sql), {'game_id': game_id}).first()
    avg_user_rating = None
    if avg_user and avg_user.TotalRating and avg_user.TotalPlayers and avg_user.TotalPlayers > 0:
        avg_user_rating = round(avg_user.TotalRating / avg_user.TotalPlayers, 1)

    director_sql = """
        SELECT d.ID, d.`Name`
        FROM Director d
        INNER JOIN GameDirectors gd ON d.ID = gd.DirectorID
        WHERE gd.GameID = :game_id
    """
    directors = db.session.execute(db.text(director_sql), {'game_id': game_id}).fetchall()

    return render_template('game.html',
                           game=game,
                           arts=arts,
                           gameplays=gameplays,
                           narratives=narratives,
                           visuals=visuals,
                           perspectives=perspectives,
                           genres=genres,
                           interfaces=interfaces,
                           pacings=pacings,
                           settings=settings,
                           first_release_date=first_release_date,
                           developers=developers,
                           publishers=publishers,
                           user_rating=user_rating,
                           platform_name=platform,
                           avg_critic_rating=avg_critic_rating,
                           avg_user_rating=avg_user_rating,
                           directors=directors)


@main_blueprint.route('/game/<int:game_id>/add-rating', methods=['GET', 'POST'])
def add_rating(game_id):
    if 'username' not in session:
        flash('Please login first', 'warning')
        return redirect(url_for('main.login'))

    game_sql = "SELECT ID, `Name` FROM Game WHERE ID = :game_id LIMIT 1"
    game = db.session.execute(db.text(game_sql), {'game_id': game_id}).first()
    if not game:
        flash('Game not found', 'error')
        return redirect(url_for('main.games'))

    platforms_sql = """
        SELECT PlatformName FROM GamesPlatform
        WHERE GameID = :game_id
        ORDER BY PlatformName
    """
    platforms_result = db.session.execute(db.text(platforms_sql), {'game_id': game_id}).fetchall()
    platforms = [(p.PlatformName, p.PlatformName) for p in platforms_result]

    rate_form = RateForm()
    rate_form.platform.choices = platforms

    if rate_form.validate_on_submit():
        rating = rate_form.rating.data
        platform = rate_form.platform.data

        check_sql = """
            SELECT Rating, PlatformName FROM UserRatings
            WHERE Username = :username AND GameID = :game_id
            LIMIT 1
        """
        existing = db.session.execute(db.text(check_sql), {
            'username': session.get('username'),
            'game_id': game_id
        }).first()

        try:
            if existing:
                old_rating = float(existing.Rating)
                old_platform = existing.PlatformName

                remove_old_sql = """
                    UPDATE GamesPlatform
                    SET TotalPlayerRating = TotalPlayerRating - :old_rating,
                        NumPlayersRated = NumPlayersRated - 1
                    WHERE GameID = :game_id AND PlatformName = :platform
                """
                db.session.execute(db.text(remove_old_sql), {
                    'old_rating': old_rating,
                    'game_id': game_id,
                    'platform': old_platform
                })

                set_null_sql = """
                    UPDATE GamesPlatform
                    SET TotalPlayerRating = NULL, NumPlayersRated = NULL
                    WHERE GameID = :game_id AND PlatformName = :platform AND NumPlayersRated <= 0
                """
                db.session.execute(db.text(set_null_sql), {
                    'game_id': game_id,
                    'platform': old_platform
                })

                add_sql = """
                    UPDATE GamesPlatform
                    SET TotalPlayerRating = COALESCE(TotalPlayerRating, 0) + :new_rating,
                        NumPlayersRated = COALESCE(NumPlayersRated, 0) + 1
                    WHERE GameID = :game_id AND PlatformName = :platform
                """
                db.session.execute(db.text(add_sql), {
                    'new_rating': float(rating),
                    'game_id': game_id,
                    'platform': platform
                })

                update_sql = """
                    UPDATE UserRatings
                    SET Rating = :new_rating, PlatformName = :new_platform
                    WHERE Username = :username AND GameID = :game_id
                """
                db.session.execute(db.text(update_sql), {
                    'new_rating': rating,
                    'new_platform': platform,
                    'username': session.get('username'),
                    'game_id': game_id
                })
                flash('Rating and platform updated successfully!', 'success')

            else:
                add_sql = """
                    UPDATE GamesPlatform
                    SET TotalPlayerRating = COALESCE(TotalPlayerRating, 0) + :new_rating,
                        NumPlayersRated = COALESCE(NumPlayersRated, 0) + 1
                    WHERE GameID = :game_id AND PlatformName = :platform
                """
                db.session.execute(db.text(add_sql), {
                    'new_rating': float(rating),
                    'game_id': game_id,
                    'platform': platform
                })

                insert_sql = """
                    INSERT INTO UserRatings (Username, PlatformName, GameID, Rating)
                    VALUES (:username, :platform, :game_id, :rating)
                """
                db.session.execute(db.text(insert_sql), {
                    'username': session.get('username'),
                    'platform': platform,
                    'game_id': game_id,
                    'rating': rating
                })
                flash('Rating added successfully!', 'success')

            db.session.commit()
            return redirect(url_for('main.game_detail', game_id=game_id))

        except Exception as e:
            db.session.rollback()
            flash(f'Error saving rating: {str(e)}', 'error')
            return redirect(url_for('main.add_rating', game_id=game_id))

    return render_template('rate.html', form=rate_form, game=game)

@main_blueprint.route('/ratings/<string:username>')
def ratings(username):
    if 'username' not in session:
        flash('Please login first', 'warning')
        return redirect(url_for('main.login'))

    if session.get('username') != username:
        flash('Unauthorized access', 'warning')
        return redirect(url_for('main.games'))

    page = request.args.get('page', 1, type=int)
    per_page = 20
    offset = (page - 1) * per_page

    count_sql = "SELECT COUNT(*) AS total FROM UserRatings INNER JOIN Game ON GameID = ID WHERE Username = :username"
    total_result = db.session.execute(db.text(count_sql), {'username': username}).first()
    total_games = total_result.total if total_result else 0

    games_sql = """
    SELECT ur.Rating, g.ID, g.`Name`, g.CoverPhoto
    FROM UserRatings ur INNER JOIN Game g
    ON ur.GameID = g.ID
    WHERE ur.Username = :username
    LIMIT :limit
    OFFSET :offset
    """
    games_result = db.session.execute(db.text(games_sql), {'username': username, 'limit':per_page, 'offset':offset}).fetchall()

    total_pages = (total_games + per_page - 1) // per_page
    has_prev = page > 1
    has_next = page < total_pages
    prev_num = None
    if has_prev:
        prev_num = page - 1
    next_num = None
    if has_next:
        next_num = page + 1

    pagination = PaginationInfo(games_result, page, total_pages, total_games, has_prev, has_next, prev_num, next_num)

    return render_template('ratings.html', games=pagination, username=username)

@main_blueprint.route('/game/<int:game_id>/releases')
def game_releases(game_id):
    if 'username' not in session:
        flash('Please login first', 'warning')
        return redirect(url_for('main.login'))

    game_sql = "SELECT ID, `Name` FROM Game WHERE ID = :game_id LIMIT 1"
    game = db.session.execute(db.text(game_sql), {'game_id': game_id}).first()

    if not game:
        flash('Game not found', 'error')
        return redirect(url_for('main.games'))

    releases_sql = """
        SELECT gp.GameID, gp.PlatformName, gp.DateOfRelease, gp.BusinessModel, gp.MaturityRating, gp.TotalPlayerRating,
            gp.NumPlayersRated, gp.AvgCriticRatingPercentage, gp.Price
        FROM GamesPlatform gp
        WHERE gp.GameID = :game_id
        ORDER BY gp.DateOfRelease , gp.PlatformName
    """

    releases_result = db.session.execute(db.text(releases_sql), {
        'game_id': game_id
    }).fetchall()

    releases = []
    for release in releases_result:
        media_types_sql = """
            SELECT MediaType FROM GamesPlatformMediaType
            WHERE GameID = :game_id AND PlatformName = :platform_name
        """
        media_types_result = db.session.execute(db.text(media_types_sql), {
            'game_id': game_id,
            'platform_name': release.PlatformName
        }).fetchall()
        media_types = [mt.MediaType for mt in media_types_result]

        input_devices_sql = """
            SELECT InputDevice FROM GamesPlatformInputDevice
            WHERE GameID = :game_id AND PlatformName = :platform_name
        """
        input_devices_result = db.session.execute(db.text(input_devices_sql), {
            'game_id': game_id,
            'platform_name': release.PlatformName
        }).fetchall()
        input_devices = [inp.InputDevice for inp in input_devices_result]

        avg_user_rating = None
        if release.TotalPlayerRating and release.NumPlayersRated and release.NumPlayersRated > 0:
            avg_user_rating = round(release.TotalPlayerRating / release.NumPlayersRated, 1)

        releases.append({
            'platform': release.PlatformName,
            'date': release.DateOfRelease,
            'business_model': release.BusinessModel,
            'maturity_rating': release.MaturityRating,
            'critic_rating': release.AvgCriticRatingPercentage,
            'user_rating': avg_user_rating,
            'price': release.Price,
            'media_types': media_types,
            'input_devices': input_devices
        })

    return render_template('game_releases.html',
                           game=game,
                           releases=releases)


@main_blueprint.route('/director/<int:director_id>')
def director_detail(director_id):
    if 'username' not in session:
        flash('Please login first', 'warning')
        return redirect(url_for('main.login'))

    director_sql = "SELECT ID, `Name`, ProfilePicture, Biography FROM Director WHERE ID = :director_id LIMIT 1"
    director = db.session.execute(db.text(director_sql), {'director_id': director_id}).first()

    if not director:
        flash('Director not found', 'error')
        return redirect(url_for('main.directors'))

    dir_count_sql = "SELECT COUNT(*) as count FROM GameDirectors WHERE DirectorID = :director_id"
    dir_count = db.session.execute(db.text(dir_count_sql), {"director_id": director_id}).first()
    num_games_directed = dir_count.count if dir_count else 0

    dir_sql = """
        SELECT AVG(gp.AvgCriticRatingPercentage) as AvgCritic,
        SUM(gp.TotalPlayerRating) / NULLIF(SUM(gp.NumPlayersRated), 0) as AvgUser
        FROM GamesPlatform gp
        INNER JOIN GameDirectors gd ON gd.GameID = gp.GameID
        WHERE gd.DirectorID = :director_id
        """

    dir = db.session.execute(db.text(dir_sql), {'director_id': director_id}).first()

    dir_avg_critic = dir.AvgCritic if dir and dir.AvgCritic else None
    dir_avg_critic = round(dir_avg_critic, 1) if dir_avg_critic else None

    dir_avg_user = None
    if dir and dir.AvgUser and dir.AvgUser > 0:
        dir_avg_user = round(dir.AvgUser, 1)

    directed_games_sql = """
        SELECT 
            g.ID,
            g.Name,
            g.CoverPhoto,
            g.MobyScore
        FROM Game g
        INNER JOIN GameDirectors gd ON g.ID = gd.GameID
        WHERE gd.DirectorID = :director_id
        """

    directed_games_result = db.session.execute(db.text(directed_games_sql), {'director_id': director_id})
    directed_games = []
    for game in directed_games_result:
        directed_games.append({
            'id': game.ID,
            'name': game.Name,
            'image': game.CoverPhoto,
            'score': game.MobyScore
        })

    websites_sql = "SELECT URL FROM DirectorWebsites WHERE DirectorID = :director_id"
    websites_result = db.session.execute(db.text(websites_sql), {'director_id': director_id})
    websites = [w.URL for w in websites_result]

    return render_template("director.html",
                           director=director,
                           num_games_directed=num_games_directed,
                           dir_avg_critic_rating=dir_avg_critic,
                           dir_avg_user_rating=dir_avg_user,
                           directed_games=directed_games,
                           websites=websites)

@main_blueprint.route('/company/<int:company_id>')
def company_detail(company_id):
    if 'username' not in session:
        flash('Please login first', 'warning')
        return redirect(url_for('main.login'))

    company_sql = "SELECT ID, `Name`, Logo, Overview, Country FROM Company WHERE ID = :company_id LIMIT 1"
    company = db.session.execute(db.text(company_sql), {'company_id': company_id}).first()

    if not company:
        flash('Company not found', 'error')
        return redirect(url_for('main.companies'))

    dev_count_sql = "SELECT COUNT(*) as count FROM CompanyDevelopGame WHERE CompanyID = :company_id"
    dev_count = db.session.execute(db.text(dev_count_sql), {'company_id': company_id}).first()
    num_games_developed = dev_count.count if dev_count else 0

    pub_count_sql = "SELECT COUNT(*) as count FROM CompanyPublishGame WHERE CompanyID = :company_id"
    pub_count = db.session.execute(db.text(pub_count_sql), {'company_id': company_id}).first()
    num_games_published = pub_count.count if pub_count else 0

    dev_sql = """
        SELECT AVG(gp.AvgCriticRatingPercentage) as AvgCritic,
        SUM(gp.TotalPlayerRating) / NULLIF(SUM(gp.NumPlayersRated), 0) as AvgUser
        FROM GamesPlatform gp
        INNER JOIN CompanyDevelopGame cdg ON gp.GameID = cdg.GameID
        WHERE cdg.CompanyID = :company_id
    """
    dev = db.session.execute(db.text(dev_sql), {'company_id': company_id}).first()
    dev_avg_critic = dev.AvgCritic if dev and dev.AvgCritic else None
    dev_avg_critic = round(dev_avg_critic, 1) if dev_avg_critic else None

    dev_avg_user = None
    if dev and dev.AvgUser and dev.AvgUser > 0:
        dev_avg_user = round(dev.AvgUser, 1)

    pub_sql = """
        SELECT AVG(gp.AvgCriticRatingPercentage) as AvgCritic, 
        SUM(gp.TotalPlayerRating) / NULLIF(SUM(gp.NumPlayersRated), 0) as AvgUser
        FROM GamesPlatform gp
        INNER JOIN CompanyPublishGame cpg ON gp.GameID = cpg.GameID
        WHERE cpg.CompanyID = :company_id
    """
    pub = db.session.execute(db.text(pub_sql), {'company_id': company_id}).first()
    pub_avg_critic = pub.AvgCritic if pub and pub.AvgCritic else None
    pub_avg_critic = round(pub_avg_critic, 1) if pub_avg_critic else None

    pub_avg_user = None
    if pub and pub.AvgUser and pub.AvgUser > 0:
        pub_avg_user = round(pub.AvgUser, 1)

    developed_games_sql = """
        SELECT 
            g.ID,
            g.Name,
            g.CoverPhoto,
            g.MobyScore
        FROM Game g
        INNER JOIN CompanyDevelopGame cdg ON g.ID = cdg.GameID
        WHERE cdg.CompanyID = :company_id
    """
    developed_games_result = db.session.execute(db.text(developed_games_sql), {'company_id': company_id}).fetchall()
    developed_games = []
    for game in developed_games_result:
        developed_games.append({
            'id': game.ID,
            'name': game.Name,
            'image': game.CoverPhoto,
            'score': game.MobyScore
        })


    published_games_sql = """
        SELECT 
            g.ID,
            g.Name,
            g.CoverPhoto,
            g.MobyScore
        FROM Game g
        INNER JOIN CompanyPublishGame cpg ON g.ID = cpg.GameID
        WHERE cpg.CompanyID = :company_id
    """
    published_games_result = db.session.execute(db.text(published_games_sql), {'company_id': company_id}).fetchall()
    published_games = []
    for game in published_games_result:
        published_games.append({
            'id': game.ID,
            'name': game.Name,
            'image': game.CoverPhoto,
            'score': game.MobyScore
        })

    websites_sql = "SELECT URL FROM CompanyWebsites WHERE CompanyID = :company_id"
    websites_result = db.session.execute(db.text(websites_sql), {'company_id': company_id}).fetchall()
    websites = [w.URL for w in websites_result]

    return render_template('company.html',
                           company=company,
                           num_games_developed=num_games_developed,
                           num_games_published=num_games_published,
                           dev_avg_critic_rating=dev_avg_critic,
                           pub_avg_critic_rating=pub_avg_critic,
                           dev_avg_user_rating=dev_avg_user,
                           pub_avg_user_rating=pub_avg_user,
                           developed_games=developed_games,
                           published_games=published_games,
                           websites=websites)


@main_blueprint.route('/platform/<path:platform_name>')
def platform_detail(platform_name):
    if 'username' not in session:
        flash('Please login first', 'warning')
        return redirect(url_for('main.login'))
    platform_name = unquote(platform_name)

    available_count_sql = "SELECT COUNT(GameID) AS count FROM GamesPlatform WHERE PlatformName = :platform_name"
    available_count = db.session.execute(db.text(available_count_sql), {'platform_name': platform_name}).first()
    if not available_count:
        flash('Platform not found', 'error')
        return redirect(url_for('main.platforms'))

    num_games_available = available_count.count if available_count else 0

    platform_sql = """
            SELECT AVG(AvgCriticRatingPercentage) as AvgCritic, 
            SUM(TotalPlayerRating) / NULLIF(SUM(NumPlayersRated), 0) as AvgUser
            FROM GamesPlatform
            WHERE PlatformName = :platform_name
        """
    platform_result = db.session.execute(db.text(platform_sql), {'platform_name': platform_name}).first()
    avg_critic_rating = round(platform_result.AvgCritic,
                              1) if platform_result and platform_result.AvgCritic else None

    avg_user_rating = None
    if platform_result and platform_result.AvgUser and platform_result.AvgUser > 0:
        avg_user_rating = round(platform_result.AvgUser, 1)


    platform = {
        'name': platform_name,
        'num_games': num_games_available,
        'avg_critic': avg_critic_rating,
        'avg_user': avg_user_rating
    }


    return render_template('platform.html', platform=platform)


@main_blueprint.route('/game_genres/<string:genre_type>/<path:name>')
def genre_detail(genre_type, name):
    if 'username' not in session:
        flash('Please login first', 'warning')
        return redirect(url_for('main.login'))

    name = unquote(name)

    possible_genre_types = ['genre', 'setting', 'gameplay', 'interface', 'perspective', 'visual', 'art', 'narrative', 'pacing']
    if genre_type not in possible_genre_types:
        flash('Invalid genre type', 'error')
        return redirect(url_for('main.game_genres'))

    table_name = genre_type.title()


    verify_sql = f"SELECT COUNT(*) AS count FROM {table_name} WHERE `Name` = :name"
    verify = db.session.execute(db.text(verify_sql), {'name': name}).first()

    if not verify or verify.count == 0:
        flash(f'{name} not found', 'error')
        return redirect(url_for('main.game_genres'))

    game_table = "Game" + table_name

    count_sql = f"""
        SELECT COUNT(DISTINCT GameID) AS count 
        FROM {game_table} 
        WHERE {table_name} = :name
    """
    count_result = db.session.execute(db.text(count_sql), {'name': name}).first()
    num_games = count_result.count if count_result else 0


    genres_sql = f"""
        SELECT AVG(gp.AvgCriticRatingPercentage) as AvgCritic, 
        SUM(gp.TotalPlayerRating) / NULLIF(SUM(gp.NumPlayersRated), 0) as AvgUser
        FROM GamesPlatform gp
        INNER JOIN {game_table} gt ON gp.GameID = gt.GameID
        WHERE gt.{table_name} = :name
    """
    genres_result = db.session.execute(db.text(genres_sql), {'name': name}).first()
    avg_critic_rating = round(genres_result.AvgCritic,
                              1) if genres_result and genres_result.AvgCritic else None

    avg_user_rating = None
    if genres_result and genres_result.AvgUser and genres_result.AvgUser > 0:
        avg_user_rating = round(genres_result.AvgUser, 1)

    genre = {
        'type': genre_type,
        'name': name,
        'num_games': num_games,
        'avg_critic': avg_critic_rating,
        'avg_user': avg_user_rating
    }

    return render_template('genre.html', genre=genre)


# Top 5 Pages
@main_blueprint.route('/top5')
def top5():
    if 'username' not in session:
        flash('Please login first', 'warning')
        return redirect(url_for('main.login'))
    return render_template('top5.html')


@main_blueprint.route('/top5/games-by-genre')
def top5_games_by_genre():
    if 'username' not in session:
        flash('Please login first', 'warning')
        return redirect(url_for('main.login'))

    genres_sql = "SELECT `Name` FROM Genre ORDER BY `Name`"
    genres_result = db.session.execute(db.text(genres_sql)).fetchall()
    genres = [g.Name for g in genres_result]

    genres_data = {}

    for genre in genres:
        top_games_sql = """
            SELECT g.ID, g.`Name`, g.CoverPhoto, g.MobyScore FROM Game g
            INNER JOIN GameGenre gg ON g.ID = gg.GameID
            WHERE gg.Genre = :genre
            AND g.MobyScore IS NOT NULL
            ORDER BY g.MobyScore DESC
            LIMIT 5
        """
        games_result = db.session.execute(db.text(top_games_sql), {'genre': genre}).fetchall()

        games_list = []
        for game in games_result:
            games_list.append({
                'id': game.ID,
                'name': game.Name,
                'image': game.CoverPhoto,
                'score': game.MobyScore
            })

        if games_list:  # Only include genres that have games
            genres_data[genre] = games_list

    return render_template('top5_games_by_genre.html', genres_data=genres_data)


@main_blueprint.route('/top5/games-by-setting')
def top5_games_by_setting():
    if 'username' not in session:
        flash('Please login first', 'warning')
        return redirect(url_for('main.login'))

    settings_sql = "SELECT `Name` FROM Setting ORDER BY `Name`"
    settings_result = db.session.execute(db.text(settings_sql)).fetchall()
    settings = [s.Name for s in settings_result]

    settings_data = {}

    for setting in settings:
        top_games_sql = """
            SELECT g.ID, g.`Name`, g.CoverPhoto, g.MobyScore FROM Game g
            INNER JOIN GameSetting gs ON g.ID = gs.GameID
            WHERE gs.Setting = :setting
            AND g.MobyScore IS NOT NULL
            ORDER BY g.MobyScore DESC
            LIMIT 5
            """
        games_result = db.session.execute(db.text(top_games_sql), {"setting": setting}).fetchall()

        games_list = []
        for game in games_result:
            games_list.append({
                'id': game.ID,
                'name': game.Name,
                'image': game.CoverPhoto,
                'score': game.MobyScore
            })

        if games_list:
            settings_data[setting] = games_list

    return render_template('top5_games_by_setting.html', settings_data=settings_data)


@main_blueprint.route('/top5/companies-by-genre')
def top5_companies_by_genre():
    if 'username' not in session:
        flash('Please login first', 'warning')
        return redirect(url_for('main.login'))

    genres_sql = "SELECT `Name` FROM Genre ORDER BY `Name`"
    genres_result = db.session.execute(db.text(genres_sql)).fetchall()
    genres = [g.Name for g in genres_result]

    company_genres_data = {}

    for genre in genres:
        top_companies_sql = """
            SELECT c.ID, c.`Name`, c.Country, c.Logo, AVG(gp.AvgCriticRatingPercentage) AS AvgCritic
            FROM Company c INNER JOIN CompanyDevelopGame cg ON c.ID = cg.CompanyID
            INNER JOIN GameGenre gg ON cg.GameID = gg.GameID AND gg.Genre = :genre
            INNER JOIN GamesPlatform gp ON cg.GameID = gp.GameID
            GROUP BY 1, 2, 3, 4
            ORDER BY 5 DESC
            LIMIT 5
            """

        companies_result = db.session.execute(db.text(top_companies_sql), {"genre": genre}).fetchall()

        companies_list = []
        for company in companies_result:
            companies_list.append({
                'id': company.ID,
                'name': company.Name,
                'logo': company.Logo,
                'country': company.Country,
                'score_percentage': round(company.AvgCritic, 1)
            })

        if companies_list:
            company_genres_data[genre] = companies_list
    return render_template('top5_companies_by_genre.html', company_genres_data=company_genres_data)


@main_blueprint.route('/top5/directors-by-volume')
def top5_directors_by_volume():
    if 'username' not in session:
        flash('Please login first', 'warning')
        return redirect(url_for('main.login'))

    directors_sql = """
        SELECT d.ID, d.`Name`, d.ProfilePicture, d.Biography, COUNT(gd.GameID) AS games_directed
        FROM Director d INNER JOIN GameDirectors gd ON d.ID = gd.DirectorID
        GROUP BY 1, 2, 3
        ORDER BY games_directed DESC
        LIMIT 5
        """

    directors_result = db.session.execute(db.text(directors_sql)).fetchall()

    return render_template('top5_directors_by_volume.html', directors_data=directors_result)



@main_blueprint.route('/top5/collaborations')
def top5_collaborations():
    if 'username' not in session:
        flash('Please login first', 'warning')
        return redirect(url_for('main.login'))

    collaborations_sql = """
        SELECT d.ID AS DirectorID, d.`Name` AS DirectorName, d.ProfilePicture,
        c.ID AS DeveloperID, c.`Name` AS DeveloperName, c.Country, c.Logo, COUNT(DISTINCT gd.GameID) AS games_collaborated
        FROM Director d INNER JOIN GameDirectors gd ON d.ID = gd.DirectorID
        INNER JOIN CompanyDevelopGame cpg ON gd.GameID = cpg.GameID
        INNER JOIN Company c ON cpg.CompanyID = c.ID
        GROUP BY 1, 2, 3, 4, 5, 6, 7
        ORDER BY 8 DESC
        LIMIT 5
        """

    collaborations_result = db.session.execute(db.text(collaborations_sql)).fetchall()
    return render_template('top5_collaborations.html', collaborations_data=collaborations_result)

# Dream Game

@main_blueprint.route('/dream-game')
def dream_game():
    if 'username' not in session:
        flash('Please login first', 'warning')
        return redirect(url_for('main.login'))

    best_dev_sql = """
        SELECT 
            c.ID,
            c.`Name`,
            c.Logo,
            SUM(gp.TotalPlayerRating) / SUM(gp.NumPlayersRated) as AvgRating
        FROM Company c
        INNER JOIN CompanyDevelopGame cdg ON c.ID = cdg.CompanyID
        INNER JOIN GamesPlatform gp ON cdg.GameID = gp.GameID
        WHERE gp.NumPlayersRated > 0
        GROUP BY c.ID, c.`Name`, c.Logo
        ORDER BY AvgRating DESC
        LIMIT 1
    """
    best_dev = db.session.execute(db.text(best_dev_sql)).first()

    best_pub_sql = """
        SELECT 
            c.ID,
            c.`Name`,
            c.Logo,
            SUM(gp.TotalPlayerRating) / SUM(gp.NumPlayersRated) as AvgRating
        FROM Company c
        INNER JOIN CompanyPublishGame cpg ON c.ID = cpg.CompanyID
        INNER JOIN GamesPlatform gp ON cpg.GameID = gp.GameID
        WHERE gp.NumPlayersRated > 0
        GROUP BY c.ID, c.`Name`, c.Logo
        ORDER BY AvgRating DESC
        LIMIT 1
    """
    best_pub = db.session.execute(db.text(best_pub_sql)).first()

    best_director_sql = """
        SELECT 
            d.ID,
            d.`Name`,
            d.ProfilePicture,
            d.Biography,
            SUM(gp.TotalPlayerRating) / SUM(gp.NumPlayersRated) as AvgRating
        FROM Director d
        INNER JOIN GameDirectors gd ON d.ID = gd.DirectorID
        INNER JOIN GamesPlatform gp ON gd.GameID = gp.GameID
        WHERE gp.NumPlayersRated > 0
        GROUP BY d.ID, d.`Name`, d.ProfilePicture, d.Biography
        ORDER BY AvgRating DESC
        LIMIT 1
    """
    best_director = db.session.execute(db.text(best_director_sql)).first()

    best_setting_sql = """
        SELECT 
            gs.Setting,
            SUM(gp.TotalPlayerRating) / SUM(gp.NumPlayersRated) as AvgRating
        FROM GameSetting gs
        INNER JOIN GamesPlatform gp ON gs.GameID = gp.GameID
        WHERE gp.NumPlayersRated > 0
        GROUP BY gs.Setting
        ORDER BY AvgRating DESC
        LIMIT 1
    """
    best_setting = db.session.execute(db.text(best_setting_sql)).first()

    best_genre_sql = """
        SELECT 
            gg.Genre,
            SUM(gp.TotalPlayerRating) / SUM(gp.NumPlayersRated) as AvgRating
        FROM GameGenre gg
        INNER JOIN GamesPlatform gp ON gg.GameID = gp.GameID
        WHERE gp.NumPlayersRated > 0
        GROUP BY gg.Genre
        ORDER BY AvgRating DESC
        LIMIT 1
    """
    best_genre = db.session.execute(db.text(best_genre_sql)).first()

    best_gameplay_sql = """
        SELECT 
            ggp.Gameplay,
            SUM(gp.TotalPlayerRating) / SUM(gp.NumPlayersRated) as AvgRating
        FROM GameGameplay ggp
        INNER JOIN GamesPlatform gp ON ggp.GameID = gp.GameID
        WHERE gp.NumPlayersRated > 0
        GROUP BY ggp.Gameplay
        ORDER BY AvgRating DESC
        LIMIT 1
    """
    best_gameplay = db.session.execute(db.text(best_gameplay_sql)).first()

    best_interface_sql = """
        SELECT 
            gi.Interface,
            SUM(gp.TotalPlayerRating) / SUM(gp.NumPlayersRated) as AvgRating
        FROM GameInterface gi
        INNER JOIN GamesPlatform gp ON gi.GameID = gp.GameID
        WHERE gp.NumPlayersRated > 0
        GROUP BY gi.Interface
        ORDER BY AvgRating DESC
        LIMIT 1
    """
    best_interface = db.session.execute(db.text(best_interface_sql)).first()

    best_perspective_sql = """
        SELECT 
            gp_attr.Perspective,
            SUM(gp.TotalPlayerRating) / SUM(gp.NumPlayersRated) as AvgRating
        FROM GamePerspective gp_attr
        INNER JOIN GamesPlatform gp ON gp_attr.GameID = gp.GameID
        WHERE gp.NumPlayersRated > 0
        GROUP BY gp_attr.Perspective
        ORDER BY AvgRating DESC
        LIMIT 1
    """
    best_perspective = db.session.execute(db.text(best_perspective_sql)).first()

    best_visual_sql = """
        SELECT 
            gv.Visual,
            SUM(gp.TotalPlayerRating) / SUM(gp.NumPlayersRated) as AvgRating
        FROM GameVisual gv
        INNER JOIN GamesPlatform gp ON gv.GameID = gp.GameID
        WHERE gp.NumPlayersRated > 0
        GROUP BY gv.Visual
        ORDER BY AvgRating DESC
        LIMIT 1
    """
    best_visual = db.session.execute(db.text(best_visual_sql)).first()

    best_narrative_sql = """
        SELECT 
            gn.Narrative,
            SUM(gp.TotalPlayerRating) / SUM(gp.NumPlayersRated) as AvgRating
        FROM GameNarrative gn
        INNER JOIN GamesPlatform gp ON gn.GameID = gp.GameID
        WHERE gp.NumPlayersRated > 0
        GROUP BY gn.Narrative
        ORDER BY AvgRating DESC
        LIMIT 1
    """
    best_narrative = db.session.execute(db.text(best_narrative_sql)).first()

    best_pacing_sql = """
        SELECT 
            gpc.Pacing,
            SUM(gp.TotalPlayerRating) / SUM(gp.NumPlayersRated) as AvgRating
        FROM GamePacing gpc
        INNER JOIN GamesPlatform gp ON gpc.GameID = gp.GameID
        WHERE gp.NumPlayersRated > 0
        GROUP BY gpc.Pacing
        ORDER BY AvgRating DESC
        LIMIT 1
    """
    best_pacing = db.session.execute(db.text(best_pacing_sql)).first()

    best_art_sql = """
        SELECT 
            ga.Art,
            SUM(gp.TotalPlayerRating) / SUM(gp.NumPlayersRated) as AvgRating
        FROM GameArt ga
        INNER JOIN GamesPlatform gp ON ga.GameID = gp.GameID
        WHERE gp.NumPlayersRated > 0
        GROUP BY ga.Art
        ORDER BY AvgRating DESC
        LIMIT 1
    """
    best_art = db.session.execute(db.text(best_art_sql)).first()

    dream_game_data = {
        'developer': {
            'id': best_dev.ID if best_dev else None,
            'name': best_dev.Name if best_dev else 'N/A',
            'logo': best_dev.Logo if best_dev else None,
            'rating': round(best_dev.AvgRating, 1) if best_dev else None
        },
        'publisher': {
            'id': best_pub.ID if best_pub else None,
            'name': best_pub.Name if best_pub else 'N/A',
            'logo': best_pub.Logo if best_pub else None,
            'rating': round(best_pub.AvgRating, 1) if best_pub else None
        },
        'director': {
            'id': best_director.ID if best_director else None,
            'name': best_director.Name if best_director else 'N/A',
            'picture': best_director.ProfilePicture if best_director else None,
            'bio': best_director.Biography if best_director else None,
            'rating': round(best_director.AvgRating, 1) if best_director else None
        },
        'setting': {
            'name': best_setting.Setting if best_setting else 'N/A',
            'rating': round(best_setting.AvgRating, 1) if best_setting else None
        },
        'genre': {
            'name': best_genre.Genre if best_genre else 'N/A',
            'rating': round(best_genre.AvgRating, 1) if best_genre else None
        },
        'gameplay': {
            'name': best_gameplay.Gameplay if best_gameplay else 'N/A',
            'rating': round(best_gameplay.AvgRating, 1) if best_gameplay else None
        },
        'interface': {
            'name': best_interface.Interface if best_interface else 'N/A',
            'rating': round(best_interface.AvgRating, 1) if best_interface else None
        },
        'perspective': {
            'name': best_perspective.Perspective if best_perspective else 'N/A',
            'rating': round(best_perspective.AvgRating, 1) if best_perspective else None
        },
        'visual': {
            'name': best_visual.Visual if best_visual else 'N/A',
            'rating': round(best_visual.AvgRating, 1) if best_visual else None
        },
        'narrative': {
            'name': best_narrative.Narrative if best_narrative else 'N/A',
            'rating': round(best_narrative.AvgRating, 1) if best_narrative else None
        },
        'pacing': {
            'name': best_pacing.Pacing if best_pacing else 'N/A',
            'rating': round(best_pacing.AvgRating, 1) if best_pacing else None
        },
        'art': {
            'name': best_art.Art if best_art else 'N/A',
            'rating': round(best_art.AvgRating, 1) if best_art else None
        }
    }

    return render_template('dream_game.html', dream_game=dream_game_data)

#Logout
@main_blueprint.route('/logout')
def logout():
    if 'username' in session:
        session.pop('username')
        flash('You have been logged out successfully', 'success')
    return redirect(url_for('main.home'))