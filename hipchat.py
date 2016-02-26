"""Small plugin for interacting with Hipchat when using bitlbee.

Supports the following commands:

/hipchat rooms
/hipchat autojoin
/hipchat whois <user>  # @mention, email, or internal id
/hipchat fullnames  # add fullnames to nicklist
/hipchat nicks <pattern>  # list nicks, optionally filter by pattern (supports *)

'rooms' and 'autojoin' both show a list of rooms - the first one all rooms, the second
one the list of rooms which you have marked as auto joining.
Whois shows information about a user. If you have img2txt installed (from libcaca) then
it will also show the profile image.

On the room list, press Alt-j to join a room.
Type 'filter x' to filter the list by x.

Auto completion
---------------

The plugin adds completion on @mentions. This needs to be enabled in weechat:

/set weechat.completion.default_template "%(nicks)|%(irc_channels)|%(hipchat_mentions)"

After that just type @<tab> to try it out.

Full name support
-----------------

When the plugin starts, it will fetch a complete list of user from Hipchat and add the full names
to the nicklist.
To disable this feature:
/set plugins.var.python.hipchat.enable_fullnames off
"""

import json
import os
import weechat

rooms_buffer = None
rooms_curline = 0
rooms_channels = []
rooms_current_sort = None
rooms_sort_inverted = False
rooms_sort_options = (
    'channel',
)
rooms_settings = (
    ('token', '', 'Hipchat token - Create on your Hipchat profile page'),
    ("autofocus", "on", "Focus the listbuffer in the current window if it isn't "
                        "already displayed by a window."),
    ("sort_order", "channel", "Last used sort order for the channel list."),
    ("sort_inverted", "off", "Invert the sort order for the channel list."),
    ("channel_min_width", "55", "The minimum width used for the channel name in "
                                "the channel list. If a channelname is shorter than "
                                "this amount, the column will be padded with spaces."),
    ('enable_fullnames', 'on', 'If on, the nicklist will also contain full names of all users'),
)
rooms_filter = None
rooms_channels_filtered = []
room_data = None
nicklist = None
nicklist_data = ''


def hipchat_cmd(data, buffer, args):
    bitlbee_server = weechat.buffer_get_string(buffer, 'name').split('.')[0]
    if buffer == weechat.buffer_search_main():
        weechat.prnt('', 'Hipchat commands must be run in IRC buffer')
        return weechat.WEECHAT_RC_ERROR

    if args.startswith('rooms'):
        room_args = args.split(' ', 1)
        if room_args[-1].startswith('**'):
            keyEvent(data, buffer, room_args[-1][2:])
            return weechat.WEECHAT_RC_OK
        rooms_initialise_list(bitlbee_server)

        weechat.hook_process("url:https://api.hipchat.com/v2/room?auth_token=%s&max-results=1000" %
                             get_token(), 30 * 1000, "room_list_cb", "")
    elif args == 'autojoin':
        rooms_initialise_list(bitlbee_server)
        nick = weechat.info_get('irc_nick', bitlbee_server)
        weechat.hook_process('url:https://api.hipchat.com/v2/user/@%s/preference/auto-join?'
                             'auth_token=%s&max-results=500' % (nick, get_token()),
                             30 * 1000, 'room_list_cb', '')
    elif args.startswith('whois'):
        whois_start(args[5:].strip())

    elif args == 'fullnames':
        update_fullnames(buffer)
    elif args.startswith('nicks'):
        show_nicks(args.split(' ', 1)[-1])

    return weechat.WEECHAT_RC_OK


def whois_start(name):
    weechat.hook_process('url:https://api.hipchat.com/v2/user/%s?auth_token=%s&max-results=500' %
                         (name, get_token()), 30 * 1000, 'whois_cb', '')


def whois_cb(data, command, rc, out, err):
    data = json.loads(out)
    if 'error' in data:
        weechat.prnt(weechat.current_buffer(), 'Failed to get user info: %s' %
                     data['error']['message'])
        return weechat.WEECHAT_RC_OK

    p = data.get('presence') or {}

    info = ('{bold}@{mention}{default} {name}, {title}\n{bold}{online}{default} ({status})\nEmail: '
            '{bold}{email}{default}\nXMPP: {xmpp}\nSince: {since}\nTimezone: {tz}').format(
        mention=encode(data['mention_name']), name=encode(data['name']), title=data['title'],
        xmpp=data['xmpp_jid'], email=data['email'],
        online='Online' if p.get('is_online') else 'Offline',
        status=p.get('status', 'unknown'),
        bold=weechat.color('bold'), default=weechat.color('reset'),
        since=data['created'],
        tz=data['timezone'])
    weechat.prnt(weechat.current_buffer(), info)

    file_name = '/tmp/hipchat_%s' % data['id']
    weechat.hook_process_hashtable('url:%s?%s' % (data['photo_url'], get_token()),
                                   {'file_out': file_name}, 15000, 'img_dl_cb', file_name)
    return weechat.WEECHAT_RC_OK


def img_dl_cb(data, command, rc, out, err):
    weechat.hook_process_hashtable('img2txt', {
        'arg1': data,
        'arg2': '-f',
        'arg3': 'ansi',
        'arg4': '-y',
        'arg5': '12'
    }, 5000, 'img_cb', '')
    return weechat.WEECHAT_RC_OK


def img_cb(data, command, rc, out, err):
    out = weechat.hook_modifier_exec('color_decode_ansi', '1', out)
    weechat.prnt(weechat.current_buffer(), out)
    return weechat.WEECHAT_RC_OK


def room_list_cb(data, command, rc, out, err):
    global rooms_channels, rooms_data

    try:
        data = json.loads(rooms_data + out)
        for d in data['items']:
            rooms_channels.append(d)

        rooms_list_end()
        if 'links' in data and 'next' in data['links']:
            weechat.hook_process('url:%s&auth_token=%s' % (data['links']['next'], get_token()),
                                 30000, 'room_list_cb', '')
        else:
            rooms_list_end()
        rooms_data = ''
    except (TypeError, ValueError):
        rooms_data += out

    return weechat.WEECHAT_RC_OK


def add_room_start(room):
    weechat.hook_process('url:https://api.hipchat.com/v2/room/%s?auth_token=%s' %
                         (room['id'], get_token()),
                         30000, 'add_room_cb', '')


def add_room_cb(data, command, rc, out, err):
    global rooms_buffer
    server = weechat.buffer_get_string(rooms_buffer, 'localvar_bitlbee_server')

    weechat.prnt('', 'Join hipchat %s' % out)
    data = json.loads(out)
    xmpp = data['xmpp_jid']
    name = xmpp.split('@')[0].split('_', 1)[1]

    weechat.command('', '/msg -server %s &bitlbee chat add hipchat %s #%s' % (server, name, name))
    weechat.command('', '/msg -server %s &bitlbee save' % server)
    weechat.command('', '/join -server %s #%s' % (server, name))
    return weechat.WEECHAT_RC_OK


# Create listbuffer.
def rooms_create_buffer(bitlbee_server):
    global rooms_buffer, rooms_curline

    if not rooms_buffer:
        rooms_buffer = weechat.buffer_new("hipchat_rooms", "rooms_input_cb",
                                          "", "rooms_close_cb", "")
        rooms_set_buffer_title()
        # Sets notify to 0 as this buffer does not need to be in hotlist.
        weechat.buffer_set(rooms_buffer, "notify", "0")
        weechat.buffer_set(rooms_buffer, "nicklist", "0")
        weechat.buffer_set(rooms_buffer, "type", "free")
        weechat.buffer_set(rooms_buffer, "key_bind_ctrl-L", "/hipchat rooms **refresh")
        weechat.buffer_set(rooms_buffer, "key_bind_meta2-A", "/hipchat rooms **up")
        weechat.buffer_set(rooms_buffer, "key_bind_meta2-B", "/hipchat rooms **down")
        weechat.buffer_set(rooms_buffer, "key_bind_meta2-1~", "/hipchat rooms **scroll_top")
        weechat.buffer_set(rooms_buffer, "key_bind_meta2-4~", "/hipchat rooms **scroll_bottom")
        weechat.buffer_set(rooms_buffer, "key_bind_meta-ctrl-J", "/hipchat rooms **enter")
        weechat.buffer_set(rooms_buffer, "key_bind_meta-ctrl-M", "/hipchat rooms **enter")
        weechat.buffer_set(rooms_buffer, "key_bind_meta->", "/hipchat rooms **sort_next")
        weechat.buffer_set(rooms_buffer, "key_bind_meta-<", "/hipchat rooms **sort_previous")
        weechat.buffer_set(rooms_buffer, "key_bind_meta-/", "/hipchat rooms **sort_invert")
        weechat.buffer_set(rooms_buffer, "localvar_set_bitlbee_server", bitlbee_server)
        rooms_curline = 0
    if weechat.config_get_plugin("autofocus") == "on":
        if not weechat.window_search_with_buffer(rooms_buffer):
            weechat.command("", "/buffer " + weechat.buffer_get_string(rooms_buffer, "name"))


def rooms_set_buffer_title():
    global rooms_buffer, rooms_curline
    ascdesc = '(v)' if rooms_sort_inverted else '(^)'
    weechat.buffer_set(rooms_buffer, "title", rooms_line_format({
        'name': 'Channel name%s' % (ascdesc if rooms_current_sort == 'channel' else ''),
        'users': 'Users%s' % (ascdesc if rooms_current_sort == 'users' else ''),
        'modes': 'Modes%s' % (ascdesc if rooms_current_sort == 'modes' else ''),
        'topic': 'Topic%s' % (ascdesc if rooms_current_sort == 'topic' else ''),
        'nomodes': None,
    }))


def rooms_initialise_list(bitlbee_server):
    global rooms_channels, rooms_curline, rooms_data

    rooms_create_buffer(bitlbee_server)
    rooms_channels = []
    rooms_data = ''
    return


def rooms_list_end():
    global rooms_current_sort

    weechat.prnt('', 'list end')

    if rooms_current_sort:
        rooms_sort()
    rooms_refresh()
    return weechat.WEECHAT_RC_OK


def keyEvent(data, buffer, args):
    global rooms_options
    rooms_options[args]()


def rooms_input_cb(data, buffer, input_data):
    global rooms_options, rooms_curline
    if input_data.startswith('filter'):
        rooms_set_filter(input_data.split(' ')[-1])
    else:
        rooms_options[input_data]()
    return weechat.WEECHAT_RC_OK


def rooms_refresh():
    global rooms_channels, rooms_buffer, rooms_channels_filtered, rooms_filter
    weechat.buffer_clear(rooms_buffer)

    rooms_channels_filtered = []
    y = 0
    for list_data in rooms_channels:
        if rooms_filter:
            if rooms_filter.lower() not in list_data['name'].lower():
                continue

        rooms_channels_filtered.append(list_data)

        rooms_refresh_line(y)
        y += 1
    return


def rooms_refresh_line(y):
    global rooms_buffer, rooms_curline, rooms_channels_filtered
    if y >= 0 and y < len(rooms_channels):
        formatted_line = rooms_line_format(rooms_channels_filtered[y], y == rooms_curline)
        weechat.prnt_y(rooms_buffer, y, formatted_line)


def rooms_refresh_curline():
    global rooms_curline
    rooms_refresh_line(rooms_curline - 1)
    rooms_refresh_line(rooms_curline)
    rooms_refresh_line(rooms_curline + 1)
    return


def rooms_line_format(list_data, curr=False):
    str = ""
    if (curr):
        str += weechat.color("yellow,red")
    channel_text = list_data['name'].ljust(int(weechat.config_get_plugin('channel_min_width')))
    str += channel_text
    str += ' (id %s)' % list_data.get('id')
    # users_text = "(%s)" % list_data['users']
    # padded_users_text = users_text.rjust(int(weechat.config_get_plugin('users_min_width')) + 2)
    # str += "%s%s %s " % (weechat.color("bold"), channel_text, padded_users_text)
    # if not list_data['nomodes']:
    #     modes = "[%s]" % list_data['modes']
    # else:
    #     modes = "[]"
    # str += "%s: " % modes.rjust(int(weechat.config_get_plugin('modes_min_width')) + 2)
    # str += "%s" % list_data['topic']
    return str


def rooms_line_up():
    global rooms_curline
    if rooms_curline <= 0:
        return
    rooms_curline -= 1
    rooms_refresh_curline()
    rooms_check_outside_window()
    return


def rooms_line_down():
    global rooms_curline, rooms_channels
    if rooms_curline + 1 >= len(rooms_channels):
        return
    rooms_curline += 1
    rooms_refresh_curline()
    rooms_check_outside_window()
    return


def rooms_line_run():
    global rooms_channels_filtered, rooms_curline
    room = rooms_channels_filtered[rooms_curline]
    add_room_start(room)
    return


def rooms_line_select():
    return


def rooms_scroll_top():
    global rooms_curline
    old_y = rooms_curline
    rooms_curline = 0
    rooms_refresh_curline()
    rooms_refresh_line(old_y)
    weechat.command(rooms_buffer, "/window scroll_top")
    return


def rooms_scroll_bottom():
    global rooms_curline, rooms_channels
    old_y = rooms_curline
    rooms_curline = len(lb_channels)-1
    rooms_refresh_curline()
    rooms_refresh_line(old_y)
    weechat.command(rooms_buffer, "/window scroll_bottom")
    return


def rooms_check_outside_window():
    global rooms_buffer, rooms_curline
    if (rooms_buffer):
        infolist = weechat.infolist_get("window", "", "current")
        if (weechat.infolist_next(infolist)):
            start_line_y = weechat.infolist_integer(infolist, "start_line_y")
            chat_height = weechat.infolist_integer(infolist, "chat_height")
            if(start_line_y > rooms_curline):
                weechat.command(rooms_buffer, "/window scroll -%i" % (start_line_y - rooms_curline))
            elif(start_line_y <= rooms_curline - chat_height):
                weechat.command(rooms_buffer, "/window scroll +%i" %
                                (rooms_curline - start_line_y - chat_height + 1))
        weechat.infolist_free(infolist)


def rooms_sort_next():
    global rooms_current_sort, rooms_sort_options
    if rooms_current_sort:
        new_index = lb_sort_options.index(rooms_current_sort) + 1
    else:
        new_index = 0

    if len(rooms_sort_options) <= new_index:
        new_index = 0

    room_set_current_sort_order(rooms_sort_options[new_index])
    room_sort()


def rooms_set_current_sort_order(value):
    global rooms_current_sort
    rooms_current_sort = value
    weechat.config_set_plugin('sort_order', rooms_current_sort)


def rooms_set_invert_sort_order(value):
    global rooms_sort_inverted
    rooms_sort_inverted = value
    weechat.config_set_plugin('sort_inverted', ('on' if rooms_sort_inverted else 'off'))


def rooms_sort_previous():
    global rooms_current_sort, rooms_sort_options
    if rooms_current_sort:
        new_index = rooms_sort_options.index(rooms_current_sort) - 1
    else:
        new_index = 0

    if new_index < 0:
        new_index = len(rooms_sort_options) - 1

    rooms_set_current_sort_order(rooms_sort_options[new_index])
    rooms_sort()


def rooms_sort(sort_key=None):
    global rooms_channels, rooms_current_sort, rooms_sort_inverted
    if sort_key:
        rooms_set_current_sort_order(sort_key)
    rooms_channels = sorted(rooms_channels, key=lambda chan_data: chan_data[rooms_current_sort])
    if rooms_sort_inverted:
        rooms_channels.reverse()
    rooms_set_buffer_title()
    rooms_refresh()


def rooms_sort_invert():
    global rooms_current_sort, rooms_sort_inverted
    if rooms_current_sort:
        rooms_set_invert_sort_order(not rooms_sort_inverted)
        rooms_sort()


def rooms_close_cb(*kwargs):
    """ A callback for buffer closing. """
    global rooms_buffer

    rooms_buffer = None
    return weechat.WEECHAT_RC_OK


def rooms_command_main(data, buffer, args):
    if args[0:2] == "**":
        keyEvent(data, buffer, args[2:])
    return weechat.WEECHAT_RC_OK


def rooms_set_default_settings():
    global rooms_settings
    # Set default settings
    for option, default_value, description in rooms_settings:
        if not weechat.config_is_set_plugin(option):
            weechat.config_set_plugin(option, default_value)
            version = weechat.info_get("version_number", "") or 0
            if int(version) >= 0x00030500:
                weechat.config_set_desc_plugin(option, description)


def rooms_reset_stored_sort_order():
    global rooms_current_sort, rooms_sort_inverted
    rooms_current_sort = weechat.config_get_plugin('sort_order')
    rooms_sort_inverted = (True if weechat.config_get_plugin('sort_inverted') == 'on' else False)


def rooms_set_filter(args):
    global rooms_filter

    rooms_filter = args
    rooms_refresh()


rooms_options = {
    'refresh': rooms_refresh,
    'up': rooms_line_up,
    'down': rooms_line_down,
    'enter': rooms_line_run,
    'space': rooms_line_select,
    'scroll_top': rooms_scroll_top,
    'scroll_bottom': rooms_scroll_bottom,
    'sort_next': rooms_sort_next,
    'sort_previous': rooms_sort_previous,
    'sort_invert': rooms_sort_invert,
}


def get_token():
    api_token = weechat.config_get_plugin('token')
    if not api_token:
        weechat.prnt('', 'Hipchat API token is required. Get one from '
                         'https://<group>.hipchat.com/account/api (View room is required) '
                         'and /set plugins.var.python.hipchat.token <token>')
    return api_token


def complete_mention(data, item, buffer, completion):
    input = decode(weechat.buffer_get_string(buffer, 'input')).split(' ')
    word = input[-1]
    if not word.startswith('@'):
        return weechat.WEECHAT_RC_OK

    search = word[1:]

    nicklist = weechat.infolist_get('nicklist', buffer, '')
    while weechat.infolist_next(nicklist):
        name = weechat.infolist_string(nicklist, 'name')
        if '|' in name:
            continue
        visible = weechat.infolist_integer(nicklist, 'visible')
        if not visible:
            continue

        if name.lower().startswith(search.lower()):
            c = '@{name}{colon}'.format(name=name, colon=':' if len(input) == 1 else '')
            weechat.hook_completion_list_add(completion, c, 0, weechat.WEECHAT_LIST_POS_SORT)

    return weechat.WEECHAT_RC_OK


def decode(s):
    if isinstance(s, str):
        s = s.decode('utf-8')
    return s


def encode(u):
    if isinstance(u, unicode):
        u = u.encode('utf-8')
    return u


def nicklist_download(url=None):
    global nicklist_data, nicklist

    f = os.path.join(hipchat_dir(), 'nicks.json')
    if os.path.exists(f):
        with open(f) as f:
            nicklist = json.load(f)
        update_all_fullnames()

    if nicklist is None:
        nicklist = {}

    if not url:
        url = 'https://api.hipchat.com/v2/user?max-results=1000'

    weechat.hook_process('url:%s&auth_token=%s' % (url, get_token()),
                         30000, 'nicklist_download_cb', '')


def nicklist_download_cb(data, command, rc, out, err):
    global nicklist, nicklist_data

    if nicklist is None:
        nicklist = {}

    try:
        data = json.loads(nicklist_data + out)
        for nick in data['items']:
            nicklist[nick['mention_name']] = nick

        nicklist_data = ''
        next = data.get('links', {}).get('next')
        if next:
            nicklist_download(next)
        else:
            f = os.path.join(hipchat_dir(), 'nicks.json')
            with open(f, 'w') as f:
                f.write(json.dumps(nicklist))
            update_all_fullnames()
            weechat.hook_signal_send('hipchat_nicks_downloaded', weechat.WEECHAT_HOOK_SIGNAL_STRING,
                                     '')
    except (TypeError, ValueError):
        if out:
            nicklist_data += out
    return weechat.WEECHAT_RC_OK


def hipchat_dir():
    path = os.path.join(weechat.info_get('weechat_dir', ''), 'hipchat')
    if not os.path.exists(path):
        weechat.mkdir_home('hipchat', 0700)
    return path


def update_all_fullnames():
    return
    b = weechat.infolist_get('buffer', '', '')
    while weechat.infolist_next(b):
        buffer = weechat.infolist_pointer(b, 'pointer')
        update_fullnames(buffer)


def update_fullnames(buffer):
    global nicklist
    nicks = weechat.infolist_get('nicklist', buffer, '')
    while weechat.infolist_next(nicks):
        name = weechat.infolist_string(nicks, 'name')
        update_fullname(buffer, name)


def update_fullname(buffer, name):
    if name not in nicklist:
        weechat.prnt('', 'Name %s not known' % name)
        return
    nick = weechat.nicklist_search_nick(buffer, '', name)
    if not nick:
        weechat.prnt('', 'Nick not found for %r in %s' % (name, buffer))
        return

    fullname = encode(nicklist[name]['name'])
    prefix = weechat.nicklist_nick_get_string(buffer, nick, 'prefix')
    if not prefix.startswith(fullname):
        prefix = '%s %s' % (fullname, prefix)
        weechat.nicklist_nick_set(buffer, nick, 'prefix', prefix)


def update_fullname_join(data, signal, signal_data):
    global nicklist
    if weechat.config_get_plugin('enable_fullnames') != 'on':
        return

    if nicklist is None:
        nicklist_download()
        return weechat.WEECHAT_RC_OK

    buffer, user = signal_data.split(',', 1)
    update_fullname(buffer, user)
    return weechat.WEECHAT_RC_OK


def show_nicks(args):
    global nicklist

    buffer = weechat.buffer_search('python', 'hipchat_nicks')
    if not buffer:
        buffer = weechat.buffer_new("hipchat_nicks", "nicks_input_cb", "", "", "")
    else:
        weechat.buffer_clear(buffer)

    weechat.buffer_set(buffer, "title", 'Hipchat users')
    weechat.buffer_set(buffer, "notify", "0")
    weechat.buffer_set(buffer, "nicklist", "0")
    weechat.buffer_set(buffer, "type", "free")
    weechat.buffer_set(buffer, 'localvar_set_hipchat_args', args)

    if nicklist is None:
        nicklist_download()
        return
    show_nicks_cb('', '', '')


def show_nicks_cb(data, signal, signal_data):
    global nicklist

    buffer = weechat.buffer_search('python', 'hipchat_nicks')
    if not buffer:
        return weechat.WEECHAT_RC_OK

    weechat.command("", "/buffer " + weechat.buffer_get_string(buffer, "name"))
    args = weechat.buffer_get_string(buffer, 'localvar_hipchat_args')

    idx = 0
    nicks = sorted(nicklist.items())
    for name, nick in nicks:
        line = '@{name} - {fullname}'.format(name=encode(name), fullname=encode(nick['name']))
        if not args or weechat.string_match(line, args, 0):
            weechat.prnt_y(buffer, idx, line)

            idx += 1

    return weechat.WEECHAT_RC_OK


def main():
    if not weechat.register('hipchat', 'Joakim Recht <recht@braindump.dk>', '1.0',
                            'MIT', 'Hipchat utilities',
                            '', ''):
        return

    rooms_set_default_settings()
    rooms_reset_stored_sort_order()
    get_token()

    weechat.hook_command(
        'hipchat', 'Hipchat utilities',
        '[rooms | autojoin | whois <user> | fullnames | nicks [<pattern>]]',
        'rooms: List rooms\nautojoin: List autojoin rooms\nwhois <user>: Get information '
        'about a specific user - either @mention or email\nfullnames: Force populate full '
        'names in nicklists in all channels\nnicks <pattern>: List users, optionally by pattern. '
        'Use * in pattern as wildcard match.\n',
        'rooms|autojoin|whois|fullnames|nicks', 'hipchat_cmd', '')
    weechat.hook_completion('hipchat_mentions', 'Mentions', 'complete_mention', '')

    if weechat.config_get_plugin('enable_fullnames') == 'on':
        nicklist_download()
    weechat.hook_signal('nicklist_nick_added', 'update_fullname_join', '')
    weechat.hook_signal('hipchat_nicks_downloaded', 'show_nicks_cb', '')


if __name__ == '__main__':
    main()
