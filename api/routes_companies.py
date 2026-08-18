import json
import os

from fastapi import APIRouter, HTTPException

from api.schemas import CompanyStatusOut
from db import repository

# Anchored to the repo root so config.json resolves regardless of where the
# server is started from. Tests patch this module global to a temp file.
CONFIG_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.json")

router = APIRouter()


def load_config():
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_config(data):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)


@router.get("/companies", response_model=list[CompanyStatusOut])
def list_companies():
    data = load_config()
    streaks = repository.list_skip_streaks()
    last_checked = repository.list_company_last_checked()
    return [
        CompanyStatusOut(
            name=company["name"],
            ats=company.get("ats", ""),
            last_checked=last_checked.get(company["name"]),
            skip_streak=streaks.get(company["name"], 0),
        )
        for company in data.get("companies", [])
    ]


@router.get("/companies/{name}", response_model=CompanyStatusOut)
def get_company(name: str):
    data = load_config()
    company = next(
        (c for c in data.get("companies", []) if c["name"] == name),
        None,
    )
    if company is None:
        raise HTTPException(status_code=404, detail=f"company '{name}' is not tracked")
    return CompanyStatusOut(
        name=company["name"],
        ats=company.get("ats", ""),
        last_checked=repository.list_company_last_checked().get(company["name"]),
        skip_streak=repository.get_skip_streak(company["name"]),
    )
