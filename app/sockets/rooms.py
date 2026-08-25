from flask_socketio import join_room, leave_room


def user_room(user_id):
    return f"user:{user_id}"


def api_room(api_id):
    return f"api:{api_id}"


def api_key_room(api_key_id):
    return f"api_key:{api_key_id}"


def join_user(user_id):
    join_room(user_room(user_id))


def leave_api(api_id):
    leave_room(api_room(api_id))


def leave_api_key(api_key_id):
    leave_room(api_key_room(api_key_id))
