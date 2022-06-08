import redis, os, hashlib, pathlib, uuid
from flask_login import LoginManager, current_user, UserMixin, AnonymousUserMixin
from flask_socketio import disconnect, emit
from flask import request, json, make_response
from binascii import hexlify
from functools import wraps


# REDIS_URL = os.environ.get("REDIS_URL")
REDIS_URL = 'redis://root:12345@127.0.0.1:6379'
login_manager = LoginManager()

"""
Connect to Redis using URL from Heroku environment.
"""
# maintain the database of logon users
logon_session = redis.Redis(
    			host="127.0.0.1",
                port=6379,
                db=0,
                password="12345",
                decode_responses=True)
# maintain the database of socket?
socket_session = redis.Redis(
    			host="127.0.0.1",
                port=6379,
                db=1,
                password="12345",
                decode_responses=True)

"""
Callback function to disconnect a user if unauthorised.
"""


"""
Checks user is authenticated by checking for an entry in redis,
if they are the wrapped function is returned else the user is disconnected.
"""


def disconnect_unauthorised(f):
    @wraps(f)
    def wrapped(*args, **kwargs):
        if (
            not current_user
            or not current_user.is_authenticated
            or not logon_session.get(current_user.session)
        ):
            print("UNAUTHORISED")
            emit("REAUTH", room=request.sid, callback=disconnect)
            return
        print(
            "AUTHORISED(Username: %s, UserId: %s, function: %s)"
            % (current_user.user, current_user.user_id, f)
        )
        return f(*args, **kwargs)

    return wrapped


"""
FlaskLoginUser class for Flask Login, added the sessionId property to use as a key in Redis
and a user_id property to have access to during queries to SQLite.
"""


class FlaskLoginUser(UserMixin):
    def __init__(self, user=None, nickname =None, user_id=None, session=None, avatar=None):
        self.user = user
        self.userNickname = nickname
        self.active = True
        self.session = session
        self.user_id = user_id
        self.avatar = avatar

    def is_active(self):
        return self.active

    def is_anonymous(self):
        return False

    def is_authenticated(self):
        return self.active

    def get_id(self):
        return self.session


"""
Flask Login user loader class, Added the retrieval of a session from Redis,
if the session doesn't exist return else instantiate a FlaskLoginUser
populating it with the values retrieved from Redis
"""


@login_manager.user_loader
def user_loader(sessionId):
    session = logon_session.get(sessionId)
    if not session:
        return
    user = json.loads(session)
    return FlaskLoginUser(user[0], user[1], user[2],sessionId, user[3])


"""
Hash plaintext password using a randomly generated salt, Return password + salt.
"""


def hash_password(pw, salt=hashlib.sha512(os.urandom(60)).hexdigest().encode("ascii")):
    #todo check the length of the pseudo password
    pwdhash = hashlib.pbkdf2_hmac("sha512", pw.encode("utf-8"), salt, 100000)
    pwdhash = hexlify(pwdhash)
    return (salt + pwdhash).decode("ascii")


"""
Verify password by hashing provided plaintext password and comparing
that to the hashed value stored in the DB.
"""


def verify_password(pw, pw2):
    return hash_password(pw, pw2[:128].encode("ascii")) == pw2


def image_handler(image, extension):
    try:
        pathlib.Path(f"./client/build/static/media/").mkdir(
            parents=True, exist_ok=False
        )
    except:
        pass
    try:
        if None or "" in {image, extension}:
            raise TypeError("Image and/or extension not provided!")

        if str(extension).lower() not in {"jpg", "png", "gif"}:
            raise ValueError("%s image format not allowed" % str(extension.lower()))

        fileName = f"{str(uuid.uuid4())}.{extension}"
        newFile = open(f"./client/build/static/media/{fileName}", "wb")
        newFile.write(bytes(image))
        newFile.close()
        return "/static/media/%s" % fileName
    except (TypeError, ValueError) as err:
        print(err)
        return False
