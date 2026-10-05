from typing import Optional
from uuid import UUID

from fastapi import HTTPException
from pydantic import BaseModel, Field, field_validator, model_validator


class FilterValue(BaseModel):
    id: UUID
    name: str = Field(default="", max_length=500)


class CustomerOrderFilter(BaseModel):
    id: UUID
    name: str = Field(min_length=1, max_length=100)
    project_id: Optional[UUID] = None
    project_name: Optional[str] = Field(default=None, max_length=500)
    sale_attribute_id: Optional[UUID] = None
    sale_dictionary_id: Optional[UUID] = None
    sale_value_id: Optional[UUID] = None
    sale_value_name: Optional[str] = Field(default=None, max_length=500)
    projects: list[FilterValue] = Field(default_factory=list, max_length=100)
    sale_values: list[FilterValue] = Field(default_factory=list, max_length=100)
    states: list[FilterValue] = Field(default_factory=list, max_length=100)
    marking_attribute_id: Optional[UUID] = None
    marking_dictionary_id: Optional[UUID] = None
    marking_values: list[FilterValue] = Field(default_factory=list, max_length=100)
    delivery_attribute_id: Optional[UUID] = None
    delivery_dictionary_id: Optional[UUID] = None
    delivery_values: list[FilterValue] = Field(default_factory=list, max_length=100)

    @model_validator(mode="before")
    @classmethod
    def migrate_single_values(cls, value):
        if not isinstance(value, dict):
            return value
        value = dict(value)
        for key, scalar, label in (("projects", "project_id", "project_name"),
                                   ("sale_values", "sale_value_id", "sale_value_name")):
            if key not in value and value.get(scalar):
                value[key] = [{"id": value[scalar], "name": value.get(label) or ""}]
        return value

    @field_validator("name")
    @classmethod
    def clean_name(cls, value):
        value = value.strip()
        if not value:
            raise ValueError("Укажите название фильтра")
        return value

    @model_validator(mode="after")
    def validate_conditions(self):
        for values in (self.projects, self.sale_values, self.states, self.marking_values, self.delivery_values):
            if len({value.id for value in values}) != len(values):
                raise ValueError("Значения фильтра не должны повторяться")
        # Explicit arrays take precedence over legacy scalar fields, including an empty array.
        self.project_id = self.projects[0].id if self.projects else None
        self.project_name = self.projects[0].name if self.projects else None
        self.sale_value_id = self.sale_values[0].id if self.sale_values else None
        self.sale_value_name = self.sale_values[0].name if self.sale_values else None
        sale = (self.sale_attribute_id, self.sale_dictionary_id, self.sale_value_id)
        if any(sale) and not all(sale):
            raise ValueError("Выберите значение поля «Где продажа»")
        for prefix, title in (("marking", "Маркировка"), ("delivery", "Тип доставки")):
            attr, dictionary, values = (getattr(self, prefix + suffix) for suffix in ('_attribute_id', '_dictionary_id', '_values'))
            if bool(values) != bool(attr and dictionary) or bool(attr) != bool(dictionary):
                raise ValueError(f"Выберите значение поля «{title}»")
        if not any((self.projects, self.sale_values, self.states, self.marking_values, self.delivery_values)):
            raise ValueError("Выберите хотя бы одно условие фильтра заказов")
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
    for project in item.projects:
        conditions.append(f"project={base_url}/entity/project/{project.id}")
    for state in item.states:
        conditions.append(f"state={base_url}/entity/customerorder/metadata/states/{state.id}")
    for sale_value in item.sale_values:
        conditions.append(f"{base_url}/entity/customerorder/metadata/attributes/{item.sale_attribute_id}="
                          f"{base_url}/entity/customentity/{item.sale_dictionary_id}/{sale_value.id}")
    for prefix in ('marking', 'delivery'):
        for value in getattr(item, prefix + '_values'):
            conditions.append(f"{base_url}/entity/customerorder/metadata/attributes/{getattr(item, prefix + '_attribute_id')}="
                              f"{base_url}/entity/customentity/{getattr(item, prefix + '_dictionary_id')}/{value.id}")
    return conditions
