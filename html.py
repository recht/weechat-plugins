"""This plugin decodes messages with <a> tags into plain text messages."""
from HTMLParser import HTMLParser
import weechat

SCRIPT_NAME = 'html'
SCRIPT_AUTHOR = 'Joakim Recht <recht@braindump.dk>'
SCRIPT_VERSION = '0.1.0'
SCRIPT_LICENSE = 'MIT'
SCRIPT_DESC = 'HTML decoding of messages'


class Parser(HTMLParser):

    def __init__(self):
        HTMLParser.__init__(self)
        self.out = []
        self.in_a = False
        self.data = None
        self.href = None

    def handle_starttag(self, tag, attrs):
        if tag == 'a':
            self.in_a = True
            self.href = dict(attrs).get('href')

    def handle_endtag(self, tag):
        if tag == 'a':
            self.in_a = False
            self.out.append(self.data or '')
            self.out.append(': ')
            self.out.append(self.href)

    def handle_data(self, data):
        if self.in_a:
            self.data = data
        else:
            self.out.append(data)


def html_decode(data, modifier, modifier_data, string):
    msg = string.split(' ', 3)
    text = msg[3][1:]
    if '<' in text and '>' in text:
        try:
            p = Parser()
            p.feed(text)

            text = ''.join((str(e) for e in p.out))
            string = '%s :%s' % (' '.join(msg[:-1]), text)
        except Exception as e:
            weechat.prnt('', 'Parse error: %s' % e)

    return string


def main():
    weechat.hook_modifier("irc_in_privmsg", "html_decode", "")


if __name__ == '__main__' and weechat.register(
    SCRIPT_NAME,
    SCRIPT_AUTHOR,
    SCRIPT_VERSION,
    SCRIPT_LICENSE,
    SCRIPT_DESC,
    '',
    ''
):
    main()
