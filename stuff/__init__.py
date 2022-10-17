from flask import *
from os import environ
from flaskext.markdown import Markdown
from flask_pyoidc.provider_configuration import *
from flask_pyoidc.flask_pyoidc import OIDCAuthentication
from boto3 import client
from stuff.auth import requirequartermaster, require_read_key
from stuff.database import *
from stuff.stuff import Item, EditItem

app = Flask(__name__)
app.config.update(
    PREFERRED_URL_SCHEME = environ.get('URL_SCHEME', 'https'),
    SECRET_KEY = environ['SECRET_KEY'],
    SERVER_NAME = environ['SERVER_NAME'],
    WTF_CSRF_ENABLED = False
)
app.jinja_env.lstrip_blocks = True
app.jinja_env.trim_blocks = True
app.url_map.strict_slashes = False

Markdown(app)
_config = ProviderConfiguration(
    environ['OIDC_ISSUER'],
    client_metadata = ClientMetadata(
        environ['OIDC_CLIENT_ID'], environ['OIDC_CLIENT_SECRET']
    )
)
_auth = OIDCAuthentication({'default': _config}, app)

_s3 = client(
    's3', aws_access_key_id = environ['S3_KEY'],
    aws_secret_access_key = environ['S3_SECRET'],
    endpoint_url = environ['S3_ENDPOINT']
)

@app.route('/api')
@require_read_key
def api():
    return jsonify(list(get_items(request.args)))

@app.route('/api/count')
@require_read_key
def api_count():
    return jsonify(get_count(request.args))

@app.route('/api/key', methods = ['GET', 'POST'])
@requirequartermaster
def api_key():
    return jsonify(generate_api_key())

@app.route('/api/keys')
@requirequartermaster
def api_keys():
    return jsonify(list(get_api_keys()))

@app.route('/api/newest')
@require_read_key
def api_newest():
    return jsonify(list(get_newest_items(request.args)))

@app.route('/api/owners')
@require_read_key
def api_owners():
    return jsonify(list(get_owners(request.args)))

@app.route('/api/random')
@app.route('/api/random/<int:sample_size>')
@require_read_key
def api_random(sample_size = 1):
    sample = list(get_random_items(request.args, sample_size))
    return jsonify(sample[0] if len(sample) == 1 else sample)

@app.route('/api/submitters')
@require_read_key
def api_submitters():
    return jsonify(list(get_submitters(request.args)))

@app.route('/delete/<game_name>', methods = ['POST'])
@_auth.oidc_auth('default')
def delete(game_name):
    if not delete_item(game_name, session['userinfo']['preferred_username']):
        abort(404)
    return redirect('/')

def _get_template_variables():
    return {
        'quartermaster': is_quartermaster(session['userinfo']['preferred_username']),
        'image_url': environ['IMAGE_URL'],
        'owners': get_owners(), 'players': get_players(),
        'submitters': get_submitters()
    }

@app.route('/')
@_auth.oidc_auth('default')
def index():
    return render_template(
        'index.html', games = get_items(request.args),
        **_get_template_variables()
    )

@app.route('/game/<game_name>')
@_auth.oidc_auth('default')
def game(game_name):
    g = get_item(game_name)
    if not g:
        abort(404)
    return render_template(
        'game.html', expansions = list(get_item_names(g['name'])), game = g,
        **_get_template_variables()
    )

@app.route('/random')
@_auth.oidc_auth('default')
def random():
    return render_template(
        'index.html', games = get_random_items(request.args, 1),
        **_get_template_variables()
    )

@app.route('/submissions')
@_auth.oidc_auth('default')
def submissions():
    return render_template(
        'submissions.html',
        games = get_submissions(
            request.args,
            session['userinfo']['preferred_username']
        ), **_get_template_variables()
    )

@app.route('/submit', methods = ['GET', 'POST'])
@_auth.oidc_auth('default')
def submit():
    if request.method == 'GET':
        return render_template(
            'submit.html',
            form = Item(session['userinfo']['preferred_username']),
            game_names = get_item_names(), **_get_template_variables()
        )
    game = Item()
    if not game.validate():
        return render_template(
            'submit.html',
            error = next(iter(game.errors.values()))[0], form = game,
            game_names = get_item_names(), **_get_template_variables()
        )
    game = game.data
    raw_info = game['info']
    game = {k: v.strip() if type(v) == str else v for k,v in game.items()}
    game['info'] = raw_info
    _s3.upload_fileobj(
        game['image'], environ['S3_BUCKET'], game['name'] + '.jpg',
        ExtraArgs = {
            'ACL': 'public-read', 'ContentType': game['image'].content_type
        }
    )
    insert_item(game, session['userinfo']['preferred_username'])
    flash('Stuff successfully submitted, thanks!')
    return redirect('/')

@app.route('/edit/<item_name>', methods = ['GET', 'POST'])
@_auth.oidc_auth('default')
def edit(item_name):
    old_item = get_item(item_name)
    if request.method == 'GET':
        print('chom!!!')
        return render_template(
            'submit.html',
            form = Item(session['userinfo']['preferred_username']),
            game_names = get_item_names(), **_get_template_variables(),
            item = old_item 
        )
    game = EditItem()
    if not game.validate():
        return render_template(
            'submit.html',
            error = next(iter(game.errors.values()))[0],
            form = game,
            game_names = get_item_names(), 
            **_get_template_variables(),
            item = get_item(item_name)
        )
    game = game.data
    game = {k: v.strip() if type(v) == str and k != 'info' else v for k,v in game.items()}
    if game['image']:
        _s3.upload_fileobj(
            game['image'], environ['S3_BUCKET'],
            f'{game["name"]}.jpg',
            ExtraArgs = {
                'ACL': 'public-read', 'ContentType': game['image'].content_type
            }
        )
    if old_item['name'] != game['name']:
        copy_source = {'Bucket': environ['S3_BUCKET'], 'Key': f'{old_item["name"]}.jpg'}
        _s3.copy_object(Bucket = environ['S3_BUCKET'], CopySource = copy_source, Key = f'{game["name"]}.jpg', ACL = 'public-read')
        _s3.delete_object(Bucket = environ['S3_BUCKET'], Key = f'{old_item["name"]}.jpg')

        #_s3.Object(environ['S3_BUCKET'],f'{game["name"]}.jpg').copy_from(CopySource=f'{environ["S3_BUCKET"]}/{old_item["name"]}')
        #_s3.Object(environ['S3_BUCKET'],f'{old_item["name"]}.jpg').delete()

    insert_item(game, session['userinfo']['preferred_username'], update = True, update_name = old_item['name'])
    flash('Game successfully submitted.')
    return redirect('/')
