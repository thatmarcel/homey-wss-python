import json
from typing import Any
import requests_async as requests
from collections.abc import Mapping
from websockets.asyncio.client import ClientConnection, connect as connect_to_websocket

class HomeyDeviceCapability:
    def __init__(self, json: Mapping) -> None:
        self.id = str(json["id"])
        self.value_type = str(json["type"])
        self.title = str(json["title"])
        self.gettable = bool(json["getable"]) # the json has "getable", not "gettable"
        self.settable = bool(json["setable"]) # the json has "setable", not "settable"
        self.value = json["value"]
        self.last_updated: int | None = json["lastUpdated"]

class HomeyDevice:
    def __init__(self, json: Mapping) -> None:
        self.id = str(json["id"])
        self.driver_id = str(json["driverId"])
        self.driver_uri = str(json["driverUri"])
        self.name = str(json["name"])
        self.device_class = str(json["class"])
        self.virtual_class = str(json["virtualClass"])
        self.capabilities: list[HomeyDeviceCapability] = list(
            HomeyDeviceCapability(capability) for capability in dict(json["capabilitiesObj"]).values()
        )
        self.settings_json = json["settings"]
        self.flags = list(json["flags"])
        self.energy_json = json["energyObj"]
        self.ui_indicator_capability_id = str(json["uiIndicator"])
        self.is_available = bool(json["available"])
        self.is_ready = bool(json["ready"])
        self.is_hidden = bool(json["hidden"])

class HomeyDriver:
    def __init__(self, json: Mapping) -> None:
        self.id = str(json["id"])
        self.owner_uri = str(json["ownerUri"])
        self.owner_name = str(json["ownerName"])
        self.owner_icon_url_path = str(json["ownerIconObj"]["url"]) if json["ownerIconObj"] else None
        self.name = str(json["name"])
        self.icon_url_path = str(json["iconObj"]["url"]) if json["iconObj"] else None
        self.color = str(json["color"])
        self.device_class = str(json["class"])
        self.is_ready = bool(json["ready"])
        self.can_pair = bool(json["pair"])
        self.can_repair = bool(json["repair"])
        self.can_unpair = bool(json["unpair"])
        self.connectivity_type = str(json["connectivity"][0]) if json["connectivity"] and len(json["connectivity"]) > 0 else None

class HomeyConnectionCredentialInternals:
    def __init__(
        self
    ) -> None:
        self.access_token: str | None = None
        self.refresh_token: str | None = None
        self.homey_id: str | None = None
        self.cloud_remote_url: str | None = None
        self.delegation_token: str | None = None
        self.cloud_remote_token: str | None = None

class HomeyClient:
    def __init__(self) -> None:
        self.connection_credential_internals = HomeyConnectionCredentialInternals()
        self._websocket: ClientConnection | None = None

    async def login(self, email: str, password: str) -> None:
        response = await requests.post(
            "https://api.athom.com/oauth2/token",
            auth=( # Retrieved from web.homey.app
                "56d8494cf8ea8fcd7952e711",
                "mtO8PWd92XbAmrXqunStqKS351E3O8XXU3NpklTB"
            ),
            data={
                "grant_type": "password",
                "username": email,
                "password": password
            }
        )
        response_json: Mapping = response.json()

        self.connection_credential_internals.access_token = response_json["access_token"]
        self.connection_credential_internals.refresh_token = response_json["refresh_token"]

    async def _fetch_homey_cloud_remote_url(self) -> None:
        assert self.connection_credential_internals.access_token

        response = await requests.get(
            "https://api.athom.com/user/me",
            headers={
                "Authorization": "Bearer " + self.connection_credential_internals.access_token
            }
        )
        response_json: Mapping = response.json()

        homeys: list[Mapping] = response_json["homeys"]
        homey_cloud = next(homey for homey in homeys if homey["platform"] == "cloud")

        self.connection_credential_internals.homey_id = homey_cloud["id"]
        self.connection_credential_internals.cloud_remote_url = homey_cloud["remoteUrl"]

    async def _fetch_delegation_token(self) -> None:
        assert self.connection_credential_internals.access_token

        response = await requests.post(
            "https://api.athom.com/delegation/token?audience=homey",
            json={},
            headers={
                "Authorization": "Bearer " + self.connection_credential_internals.access_token
            }
        )

        # The raw response is "token"
        self.connection_credential_internals.delegation_token = response.json()

    async def _fetch_cloud_remote_token(self) -> None:
        assert self.connection_credential_internals.cloud_remote_url
        assert self.connection_credential_internals.delegation_token

        response = await requests.post(
            self.connection_credential_internals.cloud_remote_url + "/api/manager/users/login",
            json={
                "token": self.connection_credential_internals.delegation_token
            }
        )

        # The raw response is "token"
        self.connection_credential_internals.cloud_remote_token = response.json()

    async def _authenticate_to_cloud_remote(self):
        await self._fetch_homey_cloud_remote_url()
        await self._fetch_delegation_token()
        await self._fetch_cloud_remote_token()

    async def connect_to_cloud_remote_websocket_after_authentication(self):
        assert self.connection_credential_internals.cloud_remote_url
        assert self.connection_credential_internals.homey_id

        self._websocket = await connect_to_websocket(
            self.connection_credential_internals.cloud_remote_url.replace("https://", "wss://") + "/socket.io/?EIO=3&transport=websocket"
        )

        _ = await self._websocket.recv()
        _ = await self._websocket.recv()

        await self._websocket.send(
            "420" + json.dumps([
                "handshakeClient",
                {
                    "token": self.connection_credential_internals.cloud_remote_token,
                    "homeyId": self.connection_credential_internals.homey_id
                }
            ])
        )

        # The message should be a string like: 430[null,{"token":"...","namespace":"/api","success":true}]
        handshake_response_message = str(await self._websocket.recv())
        assert handshake_response_message.startswith("430")

        handshake_response_message_json = json.loads(handshake_response_message[3:])
        assert handshake_response_message_json[1]["success"] is True

        await self._websocket.send("40/api,")
        api_initialize_response_message = str(await self._websocket.recv())
        assert api_initialize_response_message == "40/api,"

    async def connect_to_cloud_remote(self):
        await self._authenticate_to_cloud_remote()
        await self.connect_to_cloud_remote_websocket_after_authentication()

    async def get_devices(self) -> list[HomeyDevice]:
        assert self._websocket

        await self._websocket.send(
            "42/api,0" + json.dumps([
                "api",
                {
                    "args": {
                        "$validate": True,
                        "query": {}
                    },
                    "operation": "getDevices",
                    "uri": "homey:manager:devices"
                }
            ])
        )

        get_devices_response_message = str(await self._websocket.recv())
        assert get_devices_response_message.startswith("43/api,0")

        get_devices_response_message_json = json.loads(get_devices_response_message[8:])

        return list(HomeyDevice(device_json) for device_json in get_devices_response_message_json[1].values())

    async def get_device(self, device_id: str) -> HomeyDevice:
        assert self._websocket

        await self._websocket.send(
            "42/api,1" + json.dumps([
                "api",
                {
                    "args": {
                        "$validate": True,
                        "query": {},
                        "id": device_id
                    },
                    "operation": "getDevice",
                    "uri": "homey:manager:devices"
                }
            ])
        )

        get_device_response_message = str(await self._websocket.recv())
        assert get_device_response_message.startswith("43/api,1")

        get_device_response_message_json = json.loads(get_device_response_message[8:])

        return HomeyDevice(get_device_response_message_json[1])

    async def get_device_capability_value(self, device_id: str, capability_id: str):
        assert self._websocket

        await self._websocket.send(
            "42/api,2" + json.dumps([
                "api",
                {
                    "args": {
                        "$validate": True,
                        "deviceId": device_id,
                        "capabilityId": capability_id
                    },
                    "operation": "getCapabilityValue",
                    "uri": "homey:manager:devices"
                }
            ])
        )

        get_device_capability_value_response_message = str(await self._websocket.recv())
        assert get_device_capability_value_response_message.startswith("43/api,2")

        get_device_capability_value_response_message_json = json.loads(get_device_capability_value_response_message[8:])

        return get_device_capability_value_response_message_json[1]

    async def set_device_capability_value(self, device_id: str, capability_id: str, value: Any):
        assert self._websocket

        await self._websocket.send(
            "42/api,3" + json.dumps([
                "api",
                {
                    "args": {
                        "$validate": True,
                        "deviceId": device_id,
                        "capabilityId": capability_id,
                        "value": value
                    },
                    "operation": "setCapabilityValue",
                    "uri": "homey:manager:devices"
                }
            ])
        )

        set_device_capability_value_response_message = str(await self._websocket.recv())
        assert set_device_capability_value_response_message.startswith("43/api,3")

        assert json.loads(set_device_capability_value_response_message[8:])

    # Drivers are basically device types
    async def get_drivers(self) -> list[HomeyDriver]:
        assert self._websocket

        await self._websocket.send(
            "42/api,4" + json.dumps([
                "api",
                {
                    "args": {
                        "$validate": True,
                        "query": {}
                    },
                    "operation": "getDrivers",
                    "uri": "homey:manager:drivers"
                }
            ])
        )

        get_drivers_response_message = str(await self._websocket.recv())
        assert get_drivers_response_message.startswith("43/api,4")

        get_drivers_response_message_json = json.loads(get_drivers_response_message[8:])

        return list(HomeyDriver(driver_json) for driver_json in get_drivers_response_message_json[1])

    async def disconnect(self):
        assert self._websocket

        await self._websocket.close()
