from enum import Enum
class Role(str,Enum):
    admin='admin'; analyst='analyst'; viewer='viewer'
class RunState(str,Enum):
    queued='queued'; running='running'; completed='completed'; failed='failed'; invalidated='invalidated'
class ReviewState(str,Enum):
    draft='draft'; approved='approved'; invalidated='invalidated'
class PurgeState(str,Enum):
    purging='purging'; completed='completed'
