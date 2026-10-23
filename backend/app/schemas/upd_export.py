"""Реквизиты неподписанного исходящего УПД."""
from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class UpdParty(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    name: str = Field(default="", max_length=1000)
    inn: str = Field(default="", max_length=12)
    kpp: str = Field(default="", max_length=9)
    address: str = Field(default="", max_length=1000)
    last_name: str = Field(default="", max_length=60)
    first_name: str = Field(default="", max_length=60)
    middle_name: str = Field(default="", max_length=60)


class UpdExportOptions(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    function: Literal["СЧФДОП", "ДОП"] = "СЧФДОП"
    number: str = Field(min_length=1, max_length=1000)
    document_date: date
    sender_id: str = Field(min_length=4, max_length=100, pattern=r"^[A-Za-z0-9@.-]+$")
    receiver_id: str = Field(min_length=4, max_length=100, pattern=r"^[A-Za-z0-9@.-]+$")
    seller: UpdParty
    buyer: UpdParty
    signer_last_name: str = Field(min_length=1, max_length=60)
    signer_first_name: str = Field(min_length=1, max_length=60)
    signer_middle_name: str = Field(default="", max_length=60)
    signer_position: str = Field(min_length=1, max_length=255)
    # Первая версия — руководитель/ИП, действующий без доверенности.
    signer_is_principal: Literal[True]
    no_excise: Literal[True]
    cargo_parties_match: Literal[True]
