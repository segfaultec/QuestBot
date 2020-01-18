import redis, json
import redis_lock
import functools
import time
import pytz
from datetime import datetime
from configuration import Config

def UUID():
    with open("/proc/sys/kernel/random/uuid") as f:
        uu = f.read()[:-1]
    return uu

with open("roles.json") as f:
    Roles = json.load(f)

class DBObject:
    def serialise(self):
        data = self.__dict__
        return json.dumps(data)

    def deserialise(self, data):
        if data == None: return
        if type(data) == str or type(data) == bytes:
            data = json.loads(data)
        elif type(data) == dict:
            pass
        else:
            raise AttributeError("Invalid datatype for deserialise")
        for key, value in data.items():
            if not hasattr(self, key):
                continue
            # doing a javascript
            # i'm so sorry
            int_keys = [ # int keys are additionally checked for None or null
                "discordId", "influence", "leadership",
                "stamps", "levelReq", "leadershipReq",
                "stampReward", "playerSlots", "dm",
                "date", "influenceReward", "leadershipReward"
            ]
            bool_keys = ["admin"]
            dict_int_keys = ["players", "usedpowers"]
            if key in int_keys:
                try:
                    value = int(value)
                except Exception: # fuck you pylint
                    if value == "None" or value == "null":
                        value = None
                    pass
            if key in bool_keys:
                try:
                    value = bool(value)
                except Exception:
                    pass
            if type(value == dict) and key in dict_int_keys:
                value = {int(k):v for k, v in value.items()}
            self.__setattr__(key, value)

class Player(DBObject):
    def __init__(self, discordId=None, nick=None, jsonData=None):
        self.discordId = None
        self.nick = None
        self.influence = 300
        self.leadership = 25
        self.stamps = 8
        if discordId:
            self.discordId = discordId
            self.nick = nick
        elif jsonData:
            self.deserialise(jsonData)
        else:
            raise ValueError("Either supply discordId, or jsonData")
        self.admin = (int(self.discordId) in Config.QuestAdmins)
        self.canDM = self.admin or (not int(self.discordId) in Config.DMBlackList)
        # hardcoded nickname for jack
        # he shall be forever pale
        if self.discordId == Config.JackId:
            self.nick = "Pale"

    def getLevel(self):
        return Config.StampsToLevel(self.stamps)

    def __repr__(self):
        return f"<User {self.nick}>"

class Quest(DBObject):
    class Stamp:
        BRONZE = 1
        SILVER = 2
        GOLD = 4
        PLATINUM = 8

    def __init__(self, questId=None, jsonData=None):
        if questId == None:
            #self.questId = str(hash(time.time()))
            self.questId = UUID()
        else:
            self.questId = questId
        self.title = ""
        self.giver = ""
        self.location = ""
        self.levelReq = 5
        self.leadershipReq = 20
        self.description = ""
        self.reward = ""
        self.influenceReward = 100
        self.leadershipReward = 15
        self.stampReward = self.Stamp.SILVER
        self.commander = None
        self.players = {}
        self.playerSlots = 3
        self.dm = None
        self.date = None # stored as unix time int
        self.usedpowers = {}
        if jsonData:
            self.deserialise(jsonData)

    def __repr__(self):
        return f"<Quest {self.title}>"

    def setDate(self, newDate):
        if newDate == None:
            self.date = None
            return True, "Date unset"
        if newDate < time.time():
            return False, "Provided date is in the past"
        self.date = newDate
        return True, "Date updated"

    def getFormattedDate(self):
        if self.date == None: return "No date set"
        tz = pytz.timezone(Config.TimeZone)
        date = datetime.fromtimestamp(self.date, tz)
        return date.strftime(Config.DateFormat)

    def getFormattedUTCDate(self):
        if self.date == None: return "No date set"
        date = datetime.utcfromtimestamp(self.date)
        return date.strftime(Config.DateFormat)

    def getFormattedRewards(self):
        if self.leadershipReward == 0 and self.influenceReward == 0:
            return "None"
        rewardStr = ""
        if self.influenceReward > 0:
            rewardStr += f"{self.influenceReward} Inf"
        if self.leadershipReward > 0:
            if rewardStr != "":
                rewardStr += ", "
            rewardStr += f"{self.leadershipReward} Lds"
        return rewardStr

    def setCommander(self, player, forceAdd = False):
        if player == None:
            self.commander = None
            return True, "Success, unset commander"
        successReturn = "Success"
        if self.dm == player.discordId and not forceAdd:
            return False, "You cannot play in a quest you are DMing"
        if self.commander != None and not forceAdd:
            return False, "This quest already has a commander"
        if Config.StampsToLevel(player.stamps) < self.levelReq and not forceAdd:
            return False, "Your level does not meet the quest requirements"
        if player.leadership < self.leadershipReq and not forceAdd:
            return False, "You do not have enough leadership"
        if player.discordId in self.players.keys():
            successReturn = "Success, removed from players"
            self.removePlayer(player.discordId)
        self.commander = player.discordId
        return True, successReturn

    def setDm(self, player, forceAdd=False):
        extrainfo = ""
        if not forceAdd:
            if self.commander:
                self.setCommander(None)
                extrainfo += " (Unset commander)"
            for discordId in self.players.keys():
                self.removePlayer(discordId)
                extrainfo += " (Removed player)"
        if player == None:
            self.dm = None
            return True, "Success, unset DM"
        if self.commander == player.discordId and not forceAdd:
            return False, "You cannot DM a quest you are commanding"
        if player.discordId in self.players.keys() and not forceAdd:
            return False, "You cannot DM a quest you are playing in"
        self.dm = player.discordId
        return True, "Success" + extrainfo

    def addPlayer(self, player, removeCommander=True, forceAdd=False):
        successReturn = "Success"
        if self.dm == player.discordId and not forceAdd:
            return False, "You cannot play in a quest you are DMing"
        if len(self.players) >= self.playerSlots and not forceAdd:
            return False, "The quest is full"
        if Config.StampsToLevel(player.stamps) < self.levelReq and not forceAdd:
            return False, "Your level does not meet the quest requirements"
        if player.discordId in self.players.keys() and not forceAdd:
            return False, "You are already on this quest as an officer"
        if player.discordId == self.commander and removeCommander:
            self.commander = None
            successReturn = "Success, unset commander"
        self.players[player.discordId] = None
        return True, successReturn

    def removePlayer(self, player):
        try:
            if type(player) == Player:
                self.players.pop(player.discordId)
            elif type(player) == int:
                self.players.pop(player)
            elif type(player) == str:
                self.players.pop(int(player))
            else:
                return False, "Invalid parameter for player"
        except ValueError:
            return False, "Player wasn't found"
        return True, "Success"

    def getRole(self, roleIdToGet):
        for discordId, roleId in self.players.items():
            if roleId == roleIdToGet:
                return discordId
        return None

    def setRole(self, player, roleId, forceAdd=False):
        if not player.discordId in self.players.keys():
            if player.discordId == self.commander:
                return False, "The commander cannot have a role"
            else:
                return False, "Player is not in quest"
        if roleId == None:
            if self.players[player.discordId] == None and not forceAdd:
                return False, "This player's role is already unset"
            self.players[player.discordId] = None
            if self.usedpowers.get(player.discordId):
                self.usedpowers.pop(player.discordId)
            return True, "Success, role unset" 
        else:
            role = Roles.get(roleId)
            if role == None:
                return False, "Unknown role id"
            if self.getRole(roleId) != None and not forceAdd:
                return False, "This role is already claimed"
            if player.influence < role['cost'] and not forceAdd:
                return False, "Player has insufficient influence"
            self.players[player.discordId] = roleId
            if self.usedpowers.get(player.discordId):
                self.usedpowers.pop(player.discordId)
            return True, "Success"

    def activateRolePower(self, player, activeId):
        roleId = self.players.get(player.discordId)
        if roleId == None:
            return False, "Player is not in quest, or does not have a role."
        role = Roles[roleId]
        active = role['actives'].get(activeId)
        if self.usedpowers.get(player.discordId, 0) >= active['uses']:
            return False, "This player has fully expended this role power"
        if active == None:
            return False, "Invalid active ID for this role"
        if player.leadership < active['cost']:
            return False, "Player does not have enough leadership to activate this power"
        if self.usedpowers.get(player.discordId):
            self.usedpowers[player.discordId] += 1
        else:
            self.usedpowers[player.discordId] = 1
        return True, "Success"

    def refreshPlayerRole(self, player):
        if not self.players.get(player.discordId):
            return False, "Player is not in quest, or does not have a role."
        if not self.usedpowers.get(player.discordId):
            return False, "Player's role power is already fully refreshed."
        self.usedpowers.pop(player.discordId)
        return True, "Success"
            
def waitForLock(func):
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        return func(*args, **kwargs) # stubbing waitforlock, see how this goes
        lock = redis_lock.Lock(args[0].db, args[0].lockname)
        while not lock.acquire(blocking=True, timeout=5):
            time.sleep(0.1)
        returnData = func(*args, **kwargs)
        lock.release()
        return returnData
    return wrapper

class Database:
    db = redis.Redis()
    lockname = "QuestBot"

    def __init__(self):
        pass

    @waitForLock
    def pushTask(self, command):
        self.db.rpush("ImmTasks", command)

    @waitForLock
    def popTask(self):
        task = self.db.lpop("ImmTasks")
        if task == None: return None
        return task.decode("utf-8")

    @waitForLock
    def popAllTasks(self):
        tasks = []
        while True:
            task = self.popTask()
            if task == None:
                break
            tasks.append(task)
        return tasks

    @waitForLock
    def regeneratePlayers(self):
        for player in self.getAllPlayers():
            self.setPlayer(player)

    @waitForLock
    def regenerateQuests(self):
        for quest in self.getAllQuests():
            self.setQuest(quest)

    @waitForLock
    def lockTest(self):
        time.sleep(5)

    @waitForLock
    def setQuest(self, quest):
        self.db.hset("Quests", quest.questId, quest.serialise())

    @waitForLock
    def delQuest(self, questId, creditStamps=True):
        if creditStamps:
            quest = self.getQuest(questId)
            playersToCredit = list(quest.players.keys())
            if quest.commander: playersToCredit.append(quest.commander)
            for playerId in playersToCredit:
                player = self.getPlayer(playerId)
                startLevel = player.getLevel()
                player.stamps += quest.stampReward
                player.influence += quest.influenceReward
                player.leadership += quest.leadershipReward
                self.setPlayer(player)

                if player.getLevel() > startLevel:
                    self.pushTask("LEVELUP " + str(player.discordId))
        self.db.hdel("Quests", questId)

    @waitForLock
    def getQuest(self, questId):
        data = self.db.hget("Quests", questId)
        if data == None:
            return None
        else:
            return Quest(jsonData=data)

    @waitForLock
    def getAllQuests(self):
        data = self.db.hgetall("Quests")
        quests = []
        for questId in data.keys():
            quest = Quest(jsonData=data[questId])
            quests.append(quest)
        return quests

    @waitForLock
    def setPlayer(self, player):
        self.db.hset("Players", player.discordId, player.serialise())

    @waitForLock
    def delPlayer(self, discordId):
        self.db.hdel("Players", discordId)

    @waitForLock
    def getPlayer(self, discordId):
        data = self.db.hget("Players", discordId)
        if data == None:
            return None
        else:
            return Player(jsonData=data)

    @waitForLock
    def getAllPlayers(self):
        data = self.db.hgetall("Players")
        players = []
        for playerId in data.keys():
            player = Player(jsonData=data[playerId])
            players.append(player)
        return players

    @waitForLock
    def sanityCheck(self, memberData):
        memberDataDict = {str(x.id): x for x in memberData}

        # Remove ids from DB that shouldn't be there
        for dbPlayerId in self.db.hgetall("Players").keys():
            dbPlayerId = dbPlayerId.decode("utf-8")
            if not dbPlayerId in memberDataDict.keys():
                print(f"[SANITY] - Removing value {dbPlayerId}")
                self.db.hdel("Players", dbPlayerId)

        # Change db player nicknames if they are wrong
        for dbPlayerId, dbPlayer in self.db.hgetall("Players").items():
            member = memberDataDict.get(dbPlayerId)
            if member == None: continue
            # No need to deserialise, just edit the json
            if dbPlayer['nick'] != member.nick:
                print(f"[SANITY] - Changing nickname of {dbPlayerId}")
                dbPlayer['nick'] = member.nick
                self.db.hset("Players", dbPlayerId, dbPlayer)

        # Add players to DB that should be there
        for memberId, member in memberDataDict.items():
            if self.db.hget("Players", memberId) == None:
                print(f"[SANITY] - Adding value {member.id}")
                player = Player(member.id, member.nick)
                self.setPlayer(player)
        


        
        