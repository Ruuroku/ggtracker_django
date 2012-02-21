from __future__ import absolute_import

import binascii
import urllib2

from collections import defaultdict, namedtuple
from cStringIO import StringIO
from mpyq import MPQArchive

from sc2reader.constants import *
from sc2reader.data import DataObject
from sc2reader.utils import PersonDict, Selection, read_header, AttributeDict, Length

Location = namedtuple('Location',('x','y'))
Details = namedtuple('Details',['players','map','unknown1','unknown2','unknown3','file_time','unknown4','unknown5','unknown6','unknown7','unknown8','unknown9','unknown10','unknown11'])

MapData = namedtuple('MapData',['unknown','gateway','map_hash'])
PlayerData = namedtuple('PlayerData',['name','bnet','race','color','unknown1','unknown2','handicap','unknown3','result'])
ColorData = namedtuple('ColorData',['a','r','g','b'])
BnetData = namedtuple('BnetData',['unknown1','unknown2','subregion','uid'])
PacketData = namedtuple('Packet',['time','pid','flags','packet'])
PingData = namedtuple('Ping',['time','pid','flags','x','y'])
MessageData = namedtuple('Message',['time','pid','flags','target','text'])

class Color(AttributeDict):
    @property
    def hex(self):
        return "{0.r:02X}{0.g:02X}{0.b:02X}".format(self)

    def __str__(self):
        if not hasattr(self,'name'):
            self.name = COLOR_CODES[self.hex]
        return self.name

class Team(object):
    '''Usage:
        Team.players -> list of players
        Team.number -> int
        Team.result -> result
    '''
    def __init__(self,number):
        self.number = number
        self.players = list()
        self.result = "Unknown"

    def __iter__(self):
        return self.players.__iter__()

class Map(object):
    url_template = 'http://{0}.depot.battle.net:1119/{1}.s2ma'

    def __init__(self, gateway, map_hash, map_name=''):
        self.hash = map_hash.encode('hex')
        self.gateway = gateway
        self.url = Map.url_template.format(self.gateway, self.hash)
        self.name = map_name

    def load(self):
        print "Fetching map: {0}".format(self.url);
        self.file = urllib2.urlopen(self.url).read()
        print "Map Received"
        self.archive = MPQArchive(StringIO(self.file))
        self.minimap = self.archive.read_file('Minimap.tga')
        self.game_strings = self.archive.read_file('enUS.SC2Data\LocalizedData\GameStrings.txt')
        for line in self.game_strings.split('\r\n'):
            parts = line.split('=')
            if parts[0] == 'DocInfo/Name':
                self.name = parts[1]
            elif parts[0] == 'DocInfo/Author':
                self.author = parts[1]
            elif parts[0] == 'DocInfo/DescLong':
                self.description = parts[1]


class Replay(object):

    def __init__(self, replay_file, **options):
        #Useful references
        self.opt = AttributeDict(**options)

        # Some file-like objects may not support filenames. Issue #21
        if hasattr(replay_file, 'name'):
            self.filename = replay_file.name
        else:
            self.filename = "Unavailable"

        #header information
        self.versions,self.frames = read_header(replay_file)
        self.build = self.versions[4]
        self.release_string = "%s.%s.%s.%s" % tuple(self.versions[1:5])
        self.seconds = self.frames/16
        self.length = Length(seconds=self.seconds)

        #default values, filled in during file read
        self.player_names = list()
        self.other_people = set()
        self.speed = ""
        self.type = ""
        self.category = ""
        self.is_ladder = False
        self.is_private = False
        self.map = ""
        self.gateway = ""
        self.events = list()
        self.events_by_type = defaultdict(list)
        self.results = dict()
        self.teams = list()
        self.team = dict()
        self.observers = list() #Unordered list of Observer
        self.players = list() #Unordered list of Player
        self.player = PersonDict()
        self.people = list() #Unordered list of Players+Observers
        self.humans = list() #Unordered list of Human People
        self.person = PersonDict() #Maps pid to Player/Observer
        self.attributes = list()
        self.messages = list()
        self.recorder = None # Player object
        self.winner_known = False
        self.packets = list()
        # Set in parsers.DetailParser.load, should we hide this?
        self.file_time = None

        # TODO: Test EPOCH differences between MacOsX and Windows
        # http://en.wikipedia.org/wiki/Epoch_(reference_date)
        # Notice that Windows and Mac have different EPOCHs, I wonder whether
        # this is different depending on the OS on which the replay was played.
        self.date = None # Date when the game was played in local time
        self.utc_date = None # Date when the game was played in UTC

        self.objects = {}
        self.raw = AttributeDict()

    def load_map(self):
        self.map.load()


class Attribute(object):

    def __init__(self, data):
        #Unpack the data values and add a default name of unknown to be
        #overridden by known attributes; acts as a flag for exclusion
        self.header, self.id, self.player, self.value, self.name = tuple(data+["Unknown"])

        #Strip off the null bytes
        while self.value[-1] == '\x00': self.value = self.value[:-1]

        if self.id == 0x01F4:
            self.name, self.value = "Player Type", PLAYER_TYPE_CODES[self.value]

        elif self.id == 0x07D1:
            self.name,self.value = "Game Type", GAME_FORMAT_CODES[self.value]

        elif self.id == 0x0BB8:
            self.name, self.value = "Game Speed", GAME_SPEED_CODES[self.value]

        elif self.id == 0x0BB9:
            self.name, self.value = "Race", RACE_CODES[self.value]

        elif self.id == 0x0BBA:
            self.name, self.value = "Color", TEAM_COLOR_CODES[self.value]

        elif self.id == 0x0BBB:
            self.name = "Handicap"

        elif self.id == 0x0BBC:
            self.name, self.value = "Difficulty", DIFFICULTY_CODES[self.value]

        elif self.id == 0x0BC1:
            self.name, self.value = "Category", GAME_TYPE_CODES[self.value]

        elif self.id == 0x07D2:
            self.name = "Teams1v1"
            self.value = int(self.value[0])

        elif self.id == 0x07D3:
            self.name = "Teams2v2"
            self.value = int(self.value[0])

        elif self.id == 0x07D4:
            self.name = "Teams3v3"
            self.value = int(self.value[0])

        elif self.id == 0x07D5:
            self.name = "Teams4v4"
            self.value = int(self.value[0])

        elif self.id == 0x07D6:
            self.name = "TeamsFFA"
            self.value = int(self.value[0])

        # Complete guesses here, there are several ids that might be correct
        elif self.id == 0x07D7:
            self.name = "Teams5v5"
            self.value = int(self.value[0])

    def __repr__(self):
        return str(self)

    def __str__(self):
        return "%s: %s" % (self.name, self.value)

class Packet(object):
    def __init__(self, time, player, data):
        self.__dict__.update(locals())

class Ping(object):
    def __init__(self, time, player, x, y):
        self.time, self.player, self.location = time, player, Location(x,y)

class Message(object):

    def __init__(self, framestamp, sender, target, text):
        self.framestamp,self.sender,self.target,self.text = framestamp,sender,target,text
        self.time = Length(seconds=self.framestamp/16)
        self.to_all = (self.target == 0)
        self.to_allies = (self.target == 2)

    def __str__(self):
        return "{0:>8} - {1:<14} - {2}".format(self.time, self.sender.name, self.text)

    def __repr__(self):
        return str(self)

# Actor is a base class for Observer and Player
class Person(object):
    def __init__(self, pid, name, replay):
        self.pid = pid
        self.name = name
        self.is_observer = None
        self.messages = list()
        self.events = list()
        self.recorder = False # Actual recorder will be determined using the replay.message.events file

        self.selections = {}
        self.hotkeys = {}
        self.replay = replay

    def get_selection(self, number=10):
        """ Get selection buffer by number """
        if number < 10:
            return self.get_hotkey(number)
        else:
            selection = self.selections.get(number, Selection())
            self.selections[number] = selection
            return selection

    def get_hotkey(self, number):
        """ Get hotkey buffer by number (does not load it) """
        hotkey = self.hotkeys.get(number, Selection())
        self.hotkeys[number] = hotkey
        return hotkey

    def load_hotkey(self, number, timestamp):
        """ Push hotkey to current selection (10) """
        hotkey = self.hotkeys.get(number, Selection())
        self.hotkeys[number] = hotkey
        selection = self.get_selection(10) # get user bank
        selection.deselect_all(timestamp)
        selection.select(hotkey.current(), timestamp)

class Observer(Person):
    def __init__(self, pid, name, replay):
        super(Observer,self).__init__(pid, name, replay)
        self.is_observer = True
        self.type = 'Human'

class Player(Person):

    URL_TEMPLATE = "http://%s.battle.net/sc2/en/profile/%s/%s/%s/"

    def __init__(self, pid, name, replay):
        super(Player,self).__init__(pid, name, replay)
        self.is_observer = False
        self.aps = defaultdict(int)
        self.apm = defaultdict(int)
        self.avg_apm = 0
        # self.result = "Win","Loss","Unknown"
        # self.team = Team()
        # TODO: set default external interface variables?

    @property
    def url(self):
        return self.URL_TEMPLATE % (self.gateway, self.uid, self.subregion, self.name)

    def __str__(self):
        return "Player %s - %s (%s)" % (self.pid, self.name, self.play_race)

    def __repr__(self):
        return str(self)

    @property
    def result(self):
        return self.team.result

    def format(self, format_string):
        return format_string.format(**self.__dict__)

class Event(object):
    name = 'BaseEvent'
    def apply(self, data):
        self.data = data

    """Abstract Event Type, should not be directly instanciated"""
    def __init__(self, framestamp, player_id, event_type, event_code):
        self.frame = framestamp
        self.second = framestamp >> 4
        self.type = event_type
        self.code = event_code
        self.is_local = (player_id != 16)
        self.pid = player_id

        self.is_init = (event_type == 0x00)
        self.is_player_action = (event_type == 0x01)
        self.is_camera_movement = (event_type == 0x03)
        self.is_unknown = (event_type == 0x02 or event_type == 0x04 or event_type == 0x05)

    def _str_prefix(self):
        player_name = self.player.name if self.is_local else "Global"
        return "%s.%02i\t%-15s " % (Length(seconds=int(self.frame/16)), self.frame%16, player_name)

    def __str__(self):
        return self._str_prefix() + self.name

class UnknownEvent(Event):
    name = 'UnknownEvent'

class PlayerJoinEvent(Event):
	name = 'PlayerJoin'

class GameStartEvent(Event):
    name = 'GameStart'

class PlayerLeaveEvent(Event):
	name = 'PlayerLeave'

class CameraMovementEvent(Event):
    name = 'CameraMovement'

class ResourceTransferEvent(Event):
    name = 'ResourceTransfer'
    def __init__(self, frames, pid, type, code, target, minerals, vespene):
        super(ResourceTransferEvent, self).__init__(frames, pid, type, code)
        self.sender = pid
        self.reciever = target
        self.minerals = minerals
        self.vespene = vespene

    def __str__(self):
        return self._str_prefix() + "%s transfer %d minerals and %d gas to %s" % (self.sender, self.minerals, self.vespene, self.reciever)

class AbilityEvent(Event):
    name = 'AbilityEvent'
    def __init__(self, framestamp, player, type, code, ability):
        super(AbilityEvent, self).__init__(framestamp, player, type, code)
        self.ability = ability

    def apply(self, data):
        self.data = data
        
        if self.ability:
            if self.ability not in self.data.abilities:
                pass
                print "Unknown ability (%s) in frame %s" % (hex(self.ability),self.frame)
#                raise ValueError("Unknown ability (%s)" % (hex(self.ability)),)
            else:
                if not self.get_able_selection(self.ability):
                    for unit in self.player.get_selection().current:
                        print unit,
                        for ability in unit.abilities.iterkeys():
                            if ability:
                                print hex(ability), unit.abilities[ability]
                        print
                    print hex(self.ability)
                    print self, binascii.hexlify(self.bytes)
                    print "Using invalid ability matchup."
#                    raise ValueError("Using invalid ability matchup.")
                '''
                if able:
                    object = able[0]
                    ability = getattr(object, ability)
                    ability(self.frame)

        # claim units
        for obj in self.player.get_selection().current:
            obj.player = self.player
        '''

    def get_able_selection(self, ability):
        return [obj for obj in self.player.get_selection().current if (ability in obj.abilities)]

    def __str__(self):
        if not self.ability:
            return self._str_prefix() + "Move"
        else:
            ability_name = self.data.ability(self.ability) if self.ability in self.data.abilities else "UNKNOWN"
            return self._str_prefix() + "Ability (%s) - %s" % (hex(self.ability), ability_name)

class TargetAbilityEvent(AbilityEvent):
    name = 'TargetAbilityEvent'
    def __init__(self, framestamp, player, type, code, ability, target):
        super(TargetAbilityEvent, self).__init__(framestamp, player, type, code, ability)
        self.target = target

    def apply(self, data):
        self.data = data
        obj_id, obj_type = self.target

        try:
            obj_type = obj_type << 8 | 0x01
            type_class = self.data.types[obj_type]

            obj = None
            if (obj_id, obj_type) in self.player.replay.objects:
                obj = self.player.replay.objects[(obj_id, obj_type)]

            elif obj_id:
                obj = type_class(obj_id, self.frame)
                self.player.replay.objects[(obj_id, obj_type)] = obj

            if obj:
                obj.visit(self.frame, self.player, type_class)
                self.target = obj
            else:
                self.target = (obj_id, obj_type)
        except KeyError:
            # print "Unknown object type (%s) at frame %s" % (hex(obj_type),self.frame)
            pass
        super(TargetAbilityEvent, self).apply(data)

    def __str__(self):
        if self.target:
            if isinstance(self.target, DataObject):
                target = "{0} [{1:0>8X}]".format(self.target.name, self.target.id)
            else:
                if self.target[1] in self.data.types:
                    target = "{0} [{1:0>8X}]".format(self.data.types[self.target[1]].name,self.target[0])
                else:
                    target = "UNKNOWN {0}".format(hex(self.target[1]))

        else:
            target = "NONE"

        return AbilityEvent.__str__(self) + "; Target: {0}".format(target)

class LocationAbilityEvent(AbilityEvent):
    name = 'LocationAbilityEvent'
    def __init__(self, framestamp, player, type, code, ability, location):
        super(LocationAbilityEvent, self).__init__(framestamp, player, type, code, ability)
        self.location = location

    def __str__(self):
        return AbilityEvent.__str__(self) + "; Location: %s" % str(self.location)

class UnknownAbilityEvent(AbilityEvent):
    name = 'UnknownAbilityEvent'
    pass

class UnknownLocationAbilityEvent(AbilityEvent):
    name = 'UnknownLocationAbilityEvent'
    pass

class HotkeyEvent(Event):
    name = 'HotkeyEvent'
    def __init__(self, framestamp, player, type, code, hotkey, overlay=None):
        super(HotkeyEvent, self).__init__(framestamp, player, type, code)
        self.hotkey = hotkey
        self.overlay = overlay

    def __str__(self):
        return self._str_prefix() + "Hotkey #%d" % self.hotkey

class SetToHotkeyEvent(HotkeyEvent):
    name = 'SetToHotkeyEvent'
    def apply(self, data):
        self.data = data
        hotkey = self.player.get_hotkey(self.hotkey)
        selection = self.player.get_selection()
        hotkey[self.frame] = selection.current
        self.selected = selection.current

        # They are alive!
        for obj in selection[self.frame]:
            obj.visit(self.frame, self.player)

    def __str__(self):
        return HotkeyEvent.__str__(self) + " - Set; Selection: %s" % ', '.join(str(o) for o in self.selected)

class AddToHotkeyEvent(HotkeyEvent):
    name = 'AddToHotkeyEvent'
    def apply(self, data):
        self.data = data
        hotkey = self.player.get_hotkey(self.hotkey)
        hotkeyed = hotkey.current[:]

        # Remove from hotkey if overlay
        if self.overlay:
            hotkeyed = self.overlay(hotkeyed)

        hotkeyed.extend(self.player.get_selection()[self.frame])
        hotkeyed = list(set(hotkeyed)) # remove dups
        hotkey[self.frame] = hotkeyed
        self.selected = hotkeyed

        # They are alive!
        for obj in hotkeyed:
            obj.visit(self.frame, self.player)

    def __str__(self):
        return HotkeyEvent.__str__(self) + " - Add; Selection: %s" % ', '.join(str(o) for o in self.selected)

class GetHotkeyEvent(HotkeyEvent):
    name = 'GetHotkeyEvent'
    def apply(self, data):
        self.data = data
        hotkey = self.player.get_hotkey(self.hotkey)
        hotkeyed = hotkey.current[:]

        if self.overlay:
            hotkeyed = self.overlay(hotkeyed)

        selection = self.player.get_selection()
        selection[self.frame] = hotkeyed
        self.selected = hotkeyed

        # selection is alive!
        for obj in hotkeyed:
            obj.visit(self.frame, self.player)

    def __str__(self):
        return HotkeyEvent.__str__(self) + " - Get; Selection: %s" % ', '.join(str(o) for o in self.selected)

class SelectionEvent(Event):
    name = 'SelectionEvent'

    def __init__(self, framestamp, player, type, code, bank, objects, deselect):
        super(SelectionEvent, self).__init__(framestamp, player, type, code)
        self.bank = bank
        self.objects = objects
        self.deselect = deselect

    def apply(self, data):
        self.data = data
        selection = self.player.get_selection(self.bank)

        selected = selection.current[:]
        for obj in selected: # visit all old units
            obj.visit(self.frame, self.player)

        if self.deselect:
            selected = self.deselect(selected)

        # Add new selection
        for (obj_id, obj_type) in self.objects:
            try:
                type_class = self.data.type(obj_type)
                if (obj_id, obj_type) not in self.player.replay.objects:
                    obj = type_class(obj_id, self.frame)
                    self.player.replay.objects[(obj_id,obj_type)] = obj
                else:
                    obj = self.player.replay.objects[(obj_id,obj_type)]
                obj.visit(self.frame, self.player, type_class)
                selected.append(obj)
            except KeyError:
                print self._str_prefix() + "Selection ERROR: " + hex(obj_type)
                #raise
                # print "Unknown object type (%s) at frame %s" % (hex(obj_type),self.frame)
                #pass

        selection[self.frame] = selected
        self.selected = selected

    def __str__(self):
        if hasattr(self, "selected"):
            return self._str_prefix() + "Selection: " + ', '.join(str(o) for o in self.selected)
        else:
            return self._str_prefix() + "Selection: " + ', '.join(str(o) for o in self.objects)
