from asyncio import Lock
from datetime import datetime
from typing import Union, Tuple

from graia.ariadne.app import Ariadne
from graia.ariadne.event.message import (
    Group,
    Member,
    GroupMessage,
    MessageEvent,
    FriendMessage,
)
from graia.ariadne.message.chain import MessageChain
from graia.ariadne.message.element import At
from graia.ariadne.message.parser.twilight import (
    Twilight,
    FullMatch,
    RegexMatch,
    RegexResult,
    ArgumentMatch,
    ArgResult,
    ElementMatch,
    ElementResult,
)
from graia.ariadne.model import Friend
from graia.saya import Saya, Channel
from graia.saya.builtins.broadcast.schema import ListenerSchema
from loguru import logger
from sqlalchemy import select

from library.depend import Switch, FunctionCall, Permission
from library.model import UserPerm
from library.orm import orm
from .table import WalletBalance, WalletDetail

saya = Saya.current()
channel = Channel.current()

channel.name("Wallet")
channel.author("nullqwertyuiop")
channel.description("钱包")

update_lock = Lock()


@channel.use(
    ListenerSchema(
        listening_events=[GroupMessage],
        inline_dispatchers=[Twilight([FullMatch("钱包")])],
        decorators=[Switch.check(channel.module), FunctionCall.record(channel.module)],
    )
)
async def get_wallet(app: Ariadne, event: MessageEvent):
    if data := await Wallet.get_balance(event.sender.group, event.sender):
        balance, last_time = data
        if isinstance(last_time, datetime):
            last_time = last_time.strftime("%Y-%m-%d %H:%M:%S")
        time_line = f"\n===============\n最后一次更新于 {last_time}"
    else:
        balance = 0
        time_line = ""
    await app.send_message(
        event.sender.group if isinstance(event, GroupMessage) else event.sender,
        MessageChain(f"你一共有 {balance} 枚硬币{time_line}"),
    )


@channel.use(
    ListenerSchema(
        listening_events=[GroupMessage, FriendMessage],
        inline_dispatchers=[
            Twilight(
                [
                    FullMatch(".wallet_debug"),
                    ArgumentMatch("-f", "--field", type=int, optional=True) @ "field",
                    ArgumentMatch("-t", "--target", type=int, optional=True) @ "target",
                    ElementMatch(At) @ "at",
                    RegexMatch(r"-?[1-9][0-9]*") @ "amount",
                ]
            )
        ],
        decorators=[
            Permission.require(UserPerm.BOT_OWNER),
            Switch.check(channel.module),
            FunctionCall.record(channel.module),
        ],
    )
)
async def wallet_debug(
    app: Ariadne,
    event: MessageEvent,
    field: ArgResult,
    target: RegexResult,
    at: ElementResult,
    amount: RegexResult,
):
    if not field.matched and isinstance(event, FriendMessage):
        await app.send_friend_message(
            event.sender.id,
            MessageChain("Error: field is required for friend message"),
        )
    field = field.result if field.matched else event.sender.group
    if data := await Wallet.get_balance(event.sender.group, event.sender):
        balance, _ = data
    else:
        balance = 0
    if at.matched:
        assert isinstance(at.result, At)
        target = at.result.target
    else:
        target = target.result if target.matched else event.sender
    amount = int(amount.result.display)
    await Wallet.update(field, target, amount, "DEBUG")
    await app.send_message(
        event.sender.group if isinstance(event, GroupMessage) else event.sender,
        MessageChain(f"Balance ({target}):\n{balance} -> {balance + amount} "),
    )


class Wallet:
    @staticmethod
    def model_to_int(model: Union[Group, Member, Friend, int]):
        return model if isinstance(model, int) else model.id

    @classmethod
    async def get_balance(
        cls,
        field: Union[Group, int],
        supplicant: Union[Member, Friend, int],
    ) -> Union[None, Tuple[int, datetime]]:
        field = cls.model_to_int(field)
        supplicant = cls.model_to_int(supplicant)
        if wallet := await orm.fetchall(
            select(WalletBalance.balance, WalletBalance.time).where(
                WalletBalance.group_id == field,
                WalletBalance.member_id == supplicant,
            )
        ):
            return int(wallet[-1][0]), wallet[-1][1]
        return None

    @classmethod
    async def update(
        cls,
        field: Union[Group, int],
        supplicant: Union[Member, Friend, int],
        record: int,
        reason: str,
    ):
        field = cls.model_to_int(field)
        supplicant = cls.model_to_int(supplicant)
        await update_lock.acquire()
        if wallet := await cls.get_balance(field, supplicant):
            balance = wallet[0]
        else:
            balance = 0
        status = False
        try:
            await orm.insert_or_update(
                WalletBalance,
                [
                    WalletBalance.group_id == field,
                    WalletBalance.member_id == supplicant,
                ],
                {
                    "group_id": field,
                    "member_id": supplicant,
                    "balance": balance + record,
                    "time": datetime.now(),
                },
            )
            await orm.add(
                WalletDetail,
                {
                    "group_id": field,
                    "member_id": supplicant,
                    "record": record,
                    "reason": reason,
                    "balance": balance + record,
                    "time": datetime.now(),
                },
            )
            status = True
        except Exception as e:
            logger.error(e)
            status = False
        finally:
            update_lock.release()
            return status

    @classmethod
    async def charge(
        cls,
        group: Union[Group, int],
        member: Union[Member, int],
        record: int,
        reason: str,
    ):
        return await cls.update(group, member, (record * -1), reason)

    @classmethod
    async def get_detail(
        cls, field: Union[Group, int], supplicant: Union[Member, Friend, int]
    ):
        field = cls.model_to_int(field)
        supplicant = cls.model_to_int(supplicant)
        if wallet := await orm.fetchall(
            select(WalletDetail.record, WalletDetail.reason, WalletDetail.time).where(
                WalletDetail.group_id == field, WalletDetail.member_id == supplicant
            )
        ):
            return wallet.reverse()
        else:
            return None
