from typing import Optional
from uuid import UUID

from fastapi import HTTPException
from pydantic import BaseModel, Field, field_validator, model_validator


class CustomerOrderFilter(BaseModel):
    id: UUID
    name: str = Field(min_length=1, max_length=100)
    project_id: Optional[UUID] = None
    project_name: Optional[str] = Field(default=None, max_length=500)
    sale_attribute_id: Optional[UUID] = None
    sale_dictionary_id: Optional[UUID] = None
    sale_value_id: Optional[UUID] = None
    sale_value_name: Optional[str] = Field(default=None, max_length=500)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value):
        value = value.strip()
        if not value:
            raise ValueError("Укажите название фильтра")
        return value

    @model_validator(mode="after")
    def validate_conditions(self):
        sale = (self.sale_attribute_id, self.sale_dictionary_id, self.sale_value_id)
        if any(sale) and not all(sale):
            raise ValueError("Выберите значение поля «Где продажа»")
        if not self.project_id and not self.sale_value_id:
            raise ValueError("Выберите проект или значение поля «Где продажа»")
        return self


class CustomerOrderFilterList(BaseModel):
    filters: list[CustomerOrderFilter] = Field(default_factory=list, max_length=50)

    @model_validator(mode="after")
    def unique_filters(self):
        if len({item.id for item in self.filters}) != len(self.filters):
            raise ValueError("Идентификаторы фильтров должны быть уникальны")
        if len({item.name.casefold() for item in self.filters}) != len(self.filters):
            raise ValueError("Названия фильтров должны быть уникальны")
        return self


def profile_order_filters(profile) -> list[dict]:
    return list(getattr(profile, "customer_order_filters", None) or [])


def resolve_order_filter(profile, filter_id: Optional[UUID]) -> Optional[dict]:
    if filter_id is None:
        return None
    selected = next((item for item in profile_order_filters(profile) if item.get("id") == str(filter_id)), None)
    if selected is None:
        raise HTTPException(404, "Фильтр заказов не найден для этого юрлица. Обновите список фильтров.")
    return CustomerOrderFilter.model_validate(selected).model_dump(mode="json")


def moysklad_order_filter_conditions(base_url: str, order_filter: dict) -> list[str]:
    item = CustomerOrderFilter.model_validate(order_filter)
    conditions = []
    if item.project_id:
        conditions.append(f"project={base_url}/entity/project/{item.project_id}")
    if item.sale_value_id:
        conditions.append(f"{base_url}/entity/customerorder/metadata/attributes/{item.sale_attribute_id}="
                          f"{base_url}/entity/customentity/{item.sale_dictionary_id}/{item.sale_value_id}")
    return conditions
