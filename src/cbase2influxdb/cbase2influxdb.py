#!/usr/bin/env python3
from enum import IntEnum
from functools import partial
import os
import argparse
import aiohttp
from typing import Optional, TypeVar, Type, Annotated
from datetime import datetime
from influxdb import InfluxDBClient
from pydantic import BaseModel, Field, field_validator
import sys
import yaml
import asyncio
import logging
import csv

# Setup logging to console
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)
# Setup logging to stdout
stdout_handler = logging.StreamHandler(sys.stdout)
stdout_handler.setLevel(logging.DEBUG)
logger.addHandler(stdout_handler)

API_URL = "https://{host}/api/pvfcst_request"
API_KEY = os.environ["CBASE_API_KEY"]

class AppArgs(BaseModel):
    config_file: str
    csv_file: Optional[str]
    dry_run: bool = False

class InfluxDBConfig(BaseModel):
    host: str
    port :int = 8086
    database: str = "cbase"
    retention_policy: Optional[str]

class CBaseTrackingParam(IntEnum):
    FIXED = 0
    Y_AXIS_TRACKING = 1
    X_AXIS_TRACKING = 2
    DUAL_AXIS_TRACKING = 3



class CBaseSystemParams(BaseModel):
    latitude: float = Field(ge=-90, le=90, serialization_alias="lat")
    longitude: float = Field(ge=-90, le=90, serialization_alias="lon")
    slope: int = Field(ge=0, le=90)
    azimuth: int = Field(ge=0, le=359, serialization_alias="azi")
    tracking: CBaseTrackingParam = CBaseTrackingParam.FIXED
    panel_output: int = Field(ge=0, le=1000, serialization_alias="panel_out")
    panel_quantity: int = Field(ge=0, le=1000, serialization_alias="panel_qty")
    inverter_capacity: int = Field(default=0, ge=0, le=100000, serialization_alias="inv_cap")
    
class CBaseConfig(BaseModel):
    api_host: str
    system: CBaseSystemParams


class AppConfig(BaseModel):
    influxdb: InfluxDBConfig
    cbase: CBaseConfig

T = TypeVar('T')

def parse_na(value: str, type: Type[T]) -> Optional[T]:
    return None if value == "NA" else type(value)

class CBaseResponseData(BaseModel):
    Time_UTC: datetime = Field(alias="Time.UTC")
    temp_avg: float
    wind_avg: float
    cl_tot: float
    cl_low: float
    cl_med: float
    cl_high: float
    prec_amt: float
    s_glob: float
    s_dif: float
    s_dir_hor: float
    s_dir: float
    s_sw_net: float
    solar_angle_vs_panel: float
    albedo: float
    s_glob_pv: float
    s_ground_dif_pv: float
    s_dir_pv: float
    s_dif_pv: float
    pv_po: float
    pv_T: float | None
    pv_eta: float | None

    @field_validator("pv_T", "pv_eta", mode="before")
    def parse_na(cls, value: str) -> float | None:
        return None if value == "NA" else float(value)

class InfluxDBPoint(BaseModel):
    measurement: str
    tags: Optional[dict[str, str]]
    fields: dict[str, float]
    time: datetime

def parse_csv(csv_text: str) -> list[CBaseResponseData]:
    lines = csv_text.splitlines()
    reader = csv.DictReader(lines)
    return [CBaseResponseData(**row) for row in reader]

def csv_to_influxdb_points(csv_text: str) -> list[InfluxDBPoint]:
    weather_data_list = parse_csv(csv_text)
    return [
        InfluxDBPoint(
            measurement='cbase',
            tags={"system": "home"},
            fields = {k: v for k, v in data.model_dump(exclude={"Time_UTC"}).items() if v is not None},
            time=data.Time_UTC
        ) for data in weather_data_list
    ]

async def collect_data(
    cbase_config: CBaseConfig,
    influx: InfluxDBClient,
    influx_config: InfluxDBConfig,
    dry_run: bool = False,
) -> None:
    # Get data
    client = aiohttp.client.ClientSession()
    params = cbase_config.system.model_dump(by_alias=True)
    params["apikey"] = API_KEY
    response = await client.get(API_URL.format(host=cbase_config.api_host), params=params)
    logging.debug(response)

    # Parse response.text CSV format to InfluxDBPoints
    influxdb_points = csv_to_influxdb_points(await response.text())
    json_body = [p.model_dump() for p in influxdb_points]

    # Write to influxdb, ignore errors
    if dry_run:
        logging.debug("Send data (dry-run): %s", json_body)
    else:
        influx.write_points(json_body, retention_policy=influx_config.retention_policy)
    await client.close()


async def run(args: AppArgs) -> None:
    # Read config file
    with open(args.config_file, "r") as f:
        config = yaml.safe_load(f)
    app_config = AppConfig(**config)

    # Connect to influxdb
    influx_config = app_config.influxdb
    cbase_config = app_config.cbase
    client_params = {
        "host": influx_config.host,
        "port": influx_config.port,
        "database": influx_config.database,
    }
    influx = InfluxDBClient(**client_params)

    await collect_data(cbase_config, influx, influx_config, dry_run=args.dry_run)

def parse_args() -> AppArgs:
    parser = argparse.ArgumentParser(description='Script to collect data and write to InfluxDB.')
    parser.add_argument('config_file', type=str, help='Path to the configuration file.')
    parser.add_argument('--dry-run', action='store_true', help='Run the script in dry run mode.')
    parser.add_argument('--csv-file', help='Parse data from csv file instead of calling API.')

    args = parser.parse_args()
    return AppArgs(config_file=args.config_file, dry_run=args.dry_run, csv_file=args.csv_file)


def main() -> None:
    app_args = parse_args()
    if app_args.csv_file:
        with open(app_args.csv_file, "r") as f:
            csv_text = f.read()
        print([p.model_dump_json() for p in csv_to_influxdb_points(csv_text)])
        return
    asyncio.run(run(app_args))


if __name__ == "__main__":
    main()
