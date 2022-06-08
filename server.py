import datetime, os, eventlet, uuid
from flask import Flask, request, make_response, json, render_template
from flask_socketio import SocketIO, join_room, leave_room, disconnect
from flask_cors import CORS
from flask_login import login_user, current_user, logout_user
from numpy import single
from sqlalchemy import true,desc
from sqlalchemy_classes import Circle, User, Message, Chat, Notice
from sqlalchemy_base import Session, Base, engine
from server_helpers import (
    FlaskLoginUser,
    logon_session,
    socket_session,
    disconnect_unauthorised,
    login_manager,
    verify_password,
    image_handler,
)
eventlet.monkey_patch()

# init the flask
app = Flask(
    __name__, template_folder="./client/build", static_folder="./client/build/static"
)
CORS(app, supports_credentials=True)

# init LogManager from flask_login
login_manager.init_app(app)

# init socketIO
socket = SocketIO(app, async_mode="eventlet", cors_allowed_origins='*')

# setting the secret_key
app.config["SECRET_KEY"] = "1234567890"


"""
Catch all for react SPA.
"""


@app.route("/", defaults={"path": ""})
@app.route("/<path:path>")
def catch_all(path):
    return render_template("index.html")


"""
Register route, handles parsing user info, populating user and adding to DB.
"""


@app.route("/api/register", methods=["POST"])
def handle_register():
    try:
        # 注册需要提供的信息
        nickname = request.json.get("userNickname", None)
        username = request.json.get("username", None)
        password = request.json.get("password", None)
        firstname = request.json.get("firstname", None)
        lastname = request.json.get("lastname", None)
        email = request.json.get("email", None)

        # Check username, password, name and email are not null/empty string.
        if "" or None in {username, password, nickname, firstname, lastname, email}:
            return make_response("", 400)

        s = Session()
        # Check if user already exists.
        if s.query(User).filter(User.username == username).first():
            return make_response("", 409)

        # New user -> Create User object, add user to DB.
        user = User(nickname, username,  password, firstname, lastname, email)
        s.add(user)
        s.commit()

        # Return 201 on successful creation of user
        return make_response("", 201)

    except Exception as err:
        print(err)
        # Return 500 on error
        return make_response("", 500)
    finally:
        if "s" in locals():
            s.close()


@app.route("/api/logout", methods=["POST"])
def handle_logout():
    try:
        # get the socket_session id
        sid = socket_session.get(current_user.user_id)
        s = Session()
        login_status("SET_FRIEND_OFFLINE", current_user.user_id, s)
        # tell all the sockets the user is logout
        if sid:
            socket.emit("LOGOUT", room=sid)
            # according to the key in the two redis to delete the corresponding save
            logon_session.delete(current_user.session)
            socket_session.delete(current_user.user_id)
            # the client will recieve a disconnect message
            disconnect(sid=sid, namespace="/")
            # log out change the current_user in flasklogin
            logout_user()
        return make_response("Logged out", 200)
    except Exception as err:
        print(err)
    finally:
        if "s" in locals():
            s.close()


"""
Login route, handles verifying user provided credentials, adding session to Redis and
defining the length of the session dependant on user selecting 'remember me' or not.
"""


@app.route("/api/login", methods=["POST"])
def handle_login():
    try:
        print("--- start login!!!")
        username = request.json.get("username", None)
        password = request.json.get("password", None)
        remember = request.json.get("remember", False)

        # Check username and password are not null/empty string.
        if None or "" in {username, password}:
            return make_response("", 401)

        # Query user from DB.
        s = Session()
        user = s.query(User).filter(User.username == username).first()
        # Return a 401 if user is not found
        if not user:
            return make_response("", 401)

        # If user is in DB, verifiy password:
        if not verify_password(password, user.password):
            return make_response("", 401)

        # change the last login time
        user.last_login = datetime.datetime.utcnow()
        s.commit()

        # Create and populate SessionUser object
        print("--- username",username)
        flaskLoginUser = FlaskLoginUser(
            username, user.userNickname, user.id, str(uuid.uuid4()), user.avatar
        )

        # Store session and username in redis
        logon_session.set(
            flaskLoginUser.session, json.dumps([username, user.userNickname, user.id, user.avatar])
        )

        if not remember:
            logon_session.expireat(
                flaskLoginUser.session,
                (datetime.datetime.now() + datetime.timedelta(hours=6))
                ,
            )
        if remember:
            logon_session.expireat(
                flaskLoginUser.session,
                (datetime.datetime.now() + datetime.timedelta(days=7))
                ,
            )

        # the differnce of remember really lead to the website to remember him
        # Log user in.
        login_user(
            flaskLoginUser, remember=remember, duration=datetime.timedelta(days=7)
        )

        # Return success code along with username.
        print("--- end login!!!")
        return make_response(
            json.dumps(
                {
                    "id": user.id,
                    "username": username,
                    "userNickname": user.userNickname,
                    "avatar": user.avatar,
                    "visible_in_searches": user.visible_in_searches,
                }
            ),
            200,
            {"Content-Type": "application/json"},
        )
    except Exception as err:
        print(err)
        return make_response("", 500)
    finally:
        if "s" in locals():
            s.close()


"""
Error function to emit an error message to the client with a messaged to display.
"""


def genError(sid, msg):
    try:
        s = Session()
        user = s.query(User).get(current_user.user_id)
        singleNotice = Notice("ERROR", "System", msg)
        user.Notices.append(singleNotice)
        s.commit()
        return socket.emit(
            "ADD_NOTIFICATION",
            {
                "id": singleNotice.id,
                "type": singleNotice.type,
                "dismissed": singleNotice.dismissed,
                "message": singleNotice.message,
            },
            room=sid,
        )
    except Exception as err:
        print(err)
    finally:
        if "s" in locals():
            s.close()


"""
Retrieves the users chats from the DB, parses the data and returns a dict with the chat info
and the last 50 messages for the chat. Each chat(socketio room) then be 'joined'.
The data will then be emitted to the client.
"""


def getChats(sid, s):
    try:
        if sid in {None, ""}:
            raise ValueError("SID and/or session not provided!")
        query = s.query(User).get(current_user.user_id).chats.all()
        chats = [
            {
                "id": i.id,
                "chat_name":("、".join([ single.userNickname for single in i.users.filter(User.id != current_user.user_id)
                .all()])),
                "recipient": [ single.username for single in i.users.filter(User.id != current_user.user_id)
                .all()],
                "recipientId": [ single.id for single in i.users.filter(User.id != current_user.user_id)
                .all()],
                "active": socket_session.get(
                    i.users.filter(User.id != current_user.user_id).first().id
                )
                != None,
                "avatar": i.users.filter(User.id != current_user.user_id)
                .first()
                .avatar,
                "last_message": i.last_message,
                "last_message_timestamp": str(i.last_message_timestamp),
            }
            for i in query
        ]

        for i in chats:
            join_room(i["id"])
        socket.emit("LOAD_CHATS", chats, room=sid)

    except ValueError as err:
        print(err)
        genError(request.sid, err)


"""
Retrieves the users friends from the DB, parses the data and emits the data to the client.
"""


def getFriends(sid, s):
    try:
        print("getFriends: ", sid)
        if sid in {None, ""}:
            raise TypeError("SID and/or session not provided!")
        friends = s.query(User).get(current_user.user_id).friends
        socket.emit(
            "LOAD_FRIENDS",
            #todo change the form to the same as above chats
            [
                {
                    "id": i.id,
                    "username": i.username,
                    "userNickname": i.userNickname,
                    "avatar": i.avatar,
                    "active": socket_session.get(i.id) != None,
                }
                for i in friends
            ],
            room=sid,
        )
    except TypeError as err:
        print(err)
        genError(request.sid, err)


"""
Retrieves the users Notices from the DB, parses the data and emits to the client.
"""


def getNotices(sid, s):
    try:
        if sid in {None, ""}:
            raise TypeError("SID and/or session not provided!")
        Notices = s.query(User).get(current_user.user_id).Notices.all()
        socket.emit(
            "LOAD_NOTIFICATIONS",
            [
                {
                    "id": x.id,
                    #todo check this for what?
                    "sender": x.sender,
                    "type": x.type,
                    "dismissed": x.dismissed,
                    "message": x.message,
                    "avatar": x.avatar,
                }
                for x in Notices
            ],
            room=sid,
        )
    except (ValueError, TypeError) as err:
        print(err)
        genError(request.sid, err)

def getCircles(sid ,s):
    try:
        print("getCircles: ", sid)
        if sid in {None, ""}:
            raise TypeError("SID and/or session not provided!")
        circles = s.query(User).get(current_user.user_id).circles.order_by(desc(Circle.created_at)).limit(10).all()
        print([ str(i.created_at) for i in circles])
        socket.emit(
            "LOAD_CIRCLES",
            #todo change the form to the same as above chats
            [
                {
                    "id": i.id,
                    "userNickname": i.userNickname,
                    "avatar":
                    i.visiable.filter(User.id == i.user_id)
                    .first()
                    .avatar,
                    "circle": i.content,
                    "create_at": str(i.created_at.strftime("%Y-%m-%d %H:%M:%S"))
                }
                for i in circles
            ],
            room=sid,
        )
    except TypeError as err:
        print(err)
        genError(request.sid, err)


def login_status(status, id, s):
    try:
        friends = s.query(User).get(id).friends
        for i in friends:
            friend_sid = socket_session.get(i.id)
            if friend_sid:
                socket.emit(status, id, room=friend_sid)
    except (ValueError, TypeError) as err:
        print(err)


"""
On connection to socket, the user is added to the socket s Redis(used to determine if user is online or not),
emits chats, friends, Notices and login status.
"""


@socket.on("connect")
@disconnect_unauthorised
def handle_user_connect():
    print("--- CONNECTED_USER: %s" % current_user.user)
    try:
        # Store session details in redis
        # sid come from client
        socket_session.set(current_user.user_id, request.sid)

        # Get initial data and send to client
        s = Session()
        getChats(request.sid, s)
        getFriends(request.sid, s)
        getNotices(request.sid, s)
        #todo
        getCircles(request.sid, s)
        # Send login status to all friends
        login_status("SET_FRIEND_ONLINE", current_user.user_id, s)

    except Exception as err:
        print(err)
        genError(request.sid, err)
    finally:
        if "s" in locals():
            s.close()


"""
Removes the use from socket s Redis and emits the user's login status
"""


@socket.on("disconnect")
@disconnect_unauthorised
def handle_user_disconnect():
    try:
        print("--- DISCONNECTED: ", current_user.user)
        # Check if user is anonymous(when user logs out)
        if not current_user.is_anonymous:
            return

        # Delete the users session from Redis
        # logon_session.delete(current_user.session)
        socket_session.delete(current_user.user_id)

        # Create session and pass to login_status function
        s = Session()
        login_status("SET_FRIEND_OFFLINE", current_user.user_id, s)

    except Exception as err:
        print(err)
        genError(request.sid, err)
    finally:
        if "s" in locals():
            s.close()


"""
Emits data for all users to client, used for searching.
Excludes users that have not opted in to being visible in searches.
"""


@socket.on("LOAD_USERS")
@disconnect_unauthorised
def handle_all_accounts():
    try:
        print("--- LOAD_USERS: ", current_user.user)
        s = Session()
        query = (
            s.query(User)
            .filter(User.visible_in_searches == True , User.id != current_user.user_id)
            .all()
        )
        print(query)
        #todo clearify the room parameter meaning and usage
        socket.emit(
            "LOAD_USERS",
            [{"id": i.id, "username": i.username,"userNickname": i.userNickname, "avatar": i.avatar} for i in query],
            room=request.sid,
        )
    except Exception as err:
        print(err)
        genError(request.sid, err)
    finally:
        if "s" in locals():
            s.close()


"""
Stores message in DB and emits message to intended recipient/chat.
"""


@socket.on("ADD_MESSAGE_TO_CHAT")
@disconnect_unauthorised
def handle_new_message(data=None):
    try:
        if data.get("chat") in {None, ""}:
            raise TypeError("Chat ID not provided!")

        if data.get("message") in {None, ""}:
            if None or "" in {data.get("image"), data.get("extension")}:
                raise TypeError("Message, image and/or extension not provided.")

        s = Session()
        chat = s.query(Chat).get(data["chat"])

        if not chat:
            raise ValueError("Chat not found!")

        m = Message(current_user.user,current_user.userNickname)
        if data.get("message"):
            m.message = data["message"]
            chat.last_message = f"{data['message'][:16]}..."

        if data.get("image") and data.get("extension"):
            fileName = image_handler(data["image"], data["extension"])
            if not fileName:
                raise ValueError("File not created")
            m.image = fileName
            chat.last_message = "Image"

        chat.messages.append(m)
        chat.last_message_timestamp = m.created_at
        s.commit()

        socket.emit(
            "ADD_MESSAGE_TO_CHAT",
            {
                "chatId": data["chat"],
                "last_message": chat.last_message,
                "last_message_timestamp": str(chat.last_message_timestamp)[:5],
                "message": {
                    "username": current_user.user,
                    "userNickname": current_user.userNickname,
                    "message": m.message,
                    "id": m.id,
                    "image": m.image,
                },
            },
            room=data["chat"],
        )

    except (TypeError, ValueError) as err:
        print(err)
        genError(request.sid, err)
    finally:
        if "s" in locals():
            s.close()


"""
Loads messages for the currently active chat
"""


@socket.on("LOAD_ACTIVE_CHAT_MESSAGES")
@disconnect_unauthorised
def handleLoadMessages(chatId=None):
    try:
        if chatId in {None, ""}:
            raise TypeError("Chat id not provided!")

        s = Session()
        chat = s.query(Chat).get(chatId)

        if not chat:
            return ValueError("Invalid chat id provided!")

        messages = chat.messages.limit(50).all()
        socket.emit(
            "LOAD_ACTIVE_CHAT_MESSAGES",
            [
                {
                    "id": i.id,
                    "message": i.message,
                    "username": i.username,
                    "userNickname": i.userNickname,
                    "timestamp": str(i.created_at),
                    "image": i.image,
                }
                for i in messages
            ],
            room=request.sid,
        )

    except (ValueError, TypeError) as err:
        print(err)
        genError(request.sid, err)

    finally:
        if "s" in locals():
            s.close


"""
Creates a circle, adds the creater, Stores circle in DB and emits to the friends the circle details
"""
@socket.on("ADD_CIRCLE")
@disconnect_unauthorised
def handle_new_circle(data=None):
    try:
        if data.get("userId") in {None, ""}:
            raise TypeError("User ID not provided.")
        if data.get("circle") in {None, ""}:
            raise TypeError("Circle content not provided.")

        s = Session()
        user = s.query(User).get(data["userId"])

        if not user:
            raise ValueError("User doesn't exist")

        userNickname = user.userNickname
        userid = user.id
        friends = user.friends
        NewCircle = Circle(userNickname,userid)
        NewCircle.content =data["circle"]
        print("---receive content:",NewCircle.content)
        
        user.circles.append(NewCircle)
        for singleFriend in friends:
            singleFriend.circles.append(NewCircle)
        s.commit()

        #emit to the creater
        socket.emit(
            "ADD_CIRCLE",
            {
                "id": NewCircle.id,
                "username": user.username,
                "userNickname": NewCircle.userNickname,
                "avatar":NewCircle.visiable.filter(User.id ==NewCircle.user_id)
                    .first()
                    .avatar,
                "circle": NewCircle.content,
                "create_at":str(NewCircle.created_at.strftime("%Y-%m-%d %H:%M:%S")),
                },
            room=request.sid,
            )

        
        #emit to his friends
        for recipient in friends:
            recipient_sid = socket_session.get(recipient.id)
            if recipient_sid:
                socket.emit(
                    "ADD_CIRCLE",
                    {
                        "id": NewCircle.id,
                        "user": NewCircle.userNickname,
                        "circle": NewCircle.content,
                        "create_at":NewCircle.created_at
                    },
                    room=recipient_sid,
                )
    except (ValueError, TypeError) as err:
        print(err)
        genError(request.sid, err)
    finally:
        if "s" in locals():
            s.close()

@socket.on("ADD_CHAT")
@disconnect_unauthorised
def handle_new_group_chat(users=None):
    try:
        if users == None:
            raise TypeError("User not provided.")
        print(users)
        s = Session()

        # set the sender and recipients of the chat
        sender = s.query(User).filter(User.username == current_user.user).first()
        recipients = [s.query(User).filter(User.username == user).first() for user in users]

        if not recipients:
            raise ValueError("User doesn't exist")

        # init the chat name \ the chat id\ chat avatar
        if len(users) == 1:
            chat_avatar = recipients[0].avatar
            chat_name = recipients[0].userNickname
            
        else:
            chat_avatar = ""
            chat_name = ("、".join([ i.userNickname for i in recipients]))
        createrID=sender.id
        chat = Chat(chat_name,createrID)
        chat_id = chat.id
        

        # add chat to user
        sender.chats.append(chat)
        for recipient in recipients:
            recipient.chats.append(chat)
        s.commit()

        # set a room
        join_room(chat_id)

        socket.emit(
            "ADD_CHAT",
            {
                "id": chat_id,
                "chat_name":chat_name,
                "recipient": [recipient.username for recipient in recipients],
                "recipientId": [recipient.id for recipient in recipients],
                "avatar": chat_avatar,
            },
            room=request.sid,
        )

        # set the avatar of the chat for other recipient
        if len(users) > 1:
            recipient_chat_avatar = sender.avatar
        else:
            recipient_chat_avatar = ""

        #todo what if he dont active now?
        for recipient in recipients:
            recipient_sid = socket_session.get(recipient.id)
            member_except_now = s.query(Chat).filter(Chat.id == chat_id).first().users.filter(User.id != recipient.id).all()
            if len(member_except_now) == 1:
                chat_name = member_except_now[0]
            print([single.username for single in member_except_now])
            if recipient_sid:
                join_room(chat.id, sid=recipient_sid)
                socket.emit(
                    "ADD_CHAT",
                    {
                        "id": chat.id,
                        "chat_name":chat_name,
                        "recipient": [single.username for single in member_except_now],
                        "recipientId": [single.id for single in member_except_now],
                        "avatar": recipient_chat_avatar,
                        "last_message": "",
                        "last_message_timestamp": "",
                    },
                    room=recipient_sid,
                )
    except (ValueError, TypeError) as err:
        print(err)
        genError(request.sid, err)
    finally:
        if "s" in locals():
            s.close()

"""
If request has not already been sent the request will be added to recipients Notices,
if recipient is online(checks socket s redis) the notiifcation will be sent otherwise
the Notice will be displayed when the user next logs in.
"""


@socket.on("FRIEND_REQUEST")
@disconnect_unauthorised
def handleFriendRequest(id=None):
    print("--- FRIEND REQUEST FROM: ", current_user.user)
    print("AVATAR: ", current_user.avatar)
    try:
        if None or "" in {id}:
            raise ValueError("Id and/or Username not provided!")

        s = Session()
        recipient = s.query(User).get(id)

        if not recipient:
            raise ValueError("Recipient not found!")

        # Check they are not already friends
        if recipient.friends.filter(User.id == current_user.user_id).first():
            return False

        # Check a request has not already been sent and no
        # action has been taken by the user
        existingNotice = recipient.Notices.filter(
            Notice.sender == current_user.user,
            Notice.type == "FRIEND_REQUEST",
        ).first()

        if existingNotice:
            return False

        n = Notice(
            "FRIEND_REQUEST",
            current_user.user,
            current_user.userNickname,
            f"{current_user.userNickname} sent you a friend request",
            current_user.avatar,
        )
        recipient.Notices.append(n)
        s.commit()

        recipient_sid = socket_session.get(id)
        if recipient_sid:
            socket.emit(
                "ADD_NOTIFICATION",
                {
                    "id": n.id,
                    "type": n.type,
                    "dismissed": n.dismissed,
                    "sender": current_user.user,
                    "message": n.message,
                    "avatar": current_user.avatar,
                },
                room=recipient_sid,
            )
    except ValueError as err:
        print(err)
        genError(request.sid, err)
    finally:
        if "s" in locals():
            s.close()


"""
Adds the friend to both users, There is a better way of doing this and I will likely refactor this at a later time.
"""


@socket.on("FRIEND_REQUEST_ACCEPTED")
@disconnect_unauthorised
def handle_friend_request_accepted(data=None):
    try:
        if None or "" in {data.get("username"), data.get("id")}:
            raise TypeError("Id and/or Username not provided.")

        s = Session()

        # Get sender from DB
        sender = s.query(User).filter(User.username == data["username"]).first()

        if not sender:
            raise ValueError("Sender not found!")

        # Get recipient from DB
        recipient = s.query(User).get(current_user.user_id)

        if not recipient:
            raise ValueError("Recipient not found!")

        # Create friendship
        sender.add_friend(recipient)
        recipient.add_friend(sender)

        # Delete request Notice and emit change to client
        singleNotice = s.query(Notice).get(data["id"])
        s.delete(singleNotice)
        socket.emit("DELETE_NOTIFICATION", data["id"], room=request.sid)

        # Create Notice for request acceptance.
        n = Notice(
            "FRIEND_REQUEST_ACCEPTED",
            current_user.user,
            current_user.userNickname,
            f"{recipient.userNickname} accepted your friend request.",
            current_user.avatar,
        )
        sender.Notices.append(n)
        s.commit()

        # Send friend details to recipient (request.sid),. the user that accepted the request.
        socket.emit(
            "ADD_FRIEND",
            {
                "id": sender.id,
                "username": sender.username,
                "userNickname": sender.userNickname,
                "active": False,
                "avatar": sender.avatar,
            },
            room=request.sid,
        )

        # Check if user that sent friend request is currently online
        senderSession = socket_session.get(sender.id)
        if senderSession:
            socket.emit(
                "ADD_NOTIFICATION",
                {
                    "id":n.id,
                    "type": n.type,
                    "sender": recipient.username,
                    "senderNickname": recipient.userNickname,
                    "message": n.message,
                    "dismissed": n.dismissed,
                    "avatar": n.avatar,
                },
                room=senderSession,
            )
            socket.emit(
                "ADD_FRIEND",
                {"id": recipient.id, "username": recipient.username,"userNickname": recipient.userNickname, "active": True},
                room=senderSession,
            )

    except (ValueError, TypeError) as err:
        print(err)
        genError(request.sid, err)
    finally:
        if "s" in locals():
            s.close()


"""
Removes the Notice, Does not notify the sender.
"""


@socket.on("DELETE_NOTIFICATION")
@disconnect_unauthorised
def handle_friend_request_rejected(id=None):
    try:
        if id in {None, ""}:
            raise TypeError("Notice ID not provided.")

        s = Session()
        n = s.query(Notice).get(id)

        if n:
            s.delete(n)
            s.commit()

        # Covers situations where the DB is out of sync with the client
        socket.emit("DELETE_NOTIFICATION", id, room=request.sid)

    except (ValueError, TypeError) as err:
        print(err)
        genError(request.sid, err)
    finally:
        if "s" in locals():
            s.close()


"""
Sets the Notice to dismissed.
"""


@socket.on("DISMISS_NOTIFICATION")
@disconnect_unauthorised
def handleNoticeDismiss(id=None):
    try:
        if id in {None, ""}:
            raise TypeError("Notice ID not provided.")

        s = Session()
        n = s.query(Notice).get(id)
        if n:
            n.dismissed = True
            s.commit()

        # Covers situations where the DB is out of sync with the client
        socket.emit("DISMISS_NOTIFICATION", id, room=request.sid)
    except (ValueError, TypeError) as err:
        print(err)
        genError(request.sid, err)
    finally:
        if "s" in locals():
            s.close()


"""
Handles account updates, options avatar etc.
Need to add removal of existing avatar image when new image is uploaded.
"""


@socket.on("ACCOUNT_UPDATE")
@disconnect_unauthorised
def handleUserSettings(data=None):
    try:
        if None or "" in {data.get("update"), data.get("value")}:
            raise TypeError("Update option and/or option value not provided!")

        s = Session()
        user = s.query(User).get(current_user.user_id)

        if data["update"] == "visible_in_searches":
            user.visible_in_searches = data["value"]
            s.commit()
            socket.emit(
                "ACCOUNT_UPDATE", {data["update"]: data["value"]}, room=request.sid
            )

        if data["update"] == "avatar":
            fileName = image_handler(data["value"], data["extension"])
            if fileName:
                user.avatar = fileName
                s.commit()
                socket.emit(
                    "ACCOUNT_UPDATE", {data["update"]: user.avatar}, room=request.sid
                )
            else:
                raise ValueError("Filename not generated")

    except (ValueError, TypeError) as err:
        print(err)
        genError(request.sid, err)
    finally:
        if "s" in locals():
            s.close()

# def ack():
#     print("connected success")
# @socket.on("connect",namespace="/chat")
# @disconnect_unauthorised
# def handle_check():
#     print("check over,this connected is ok!")
#     print(request.sid)
#     socket.emit("OVER",room=request.sid,callback=ack)
if __name__ == "__main__":
    socket.run(app, debug=True)
