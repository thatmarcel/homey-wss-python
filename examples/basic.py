import asyncio

from homey_wss_python import HomeyClient

async def main():
    homey_email_address = input("Homey email address: ")
    homey_password = input("Homey password: ")

    client = HomeyClient()

    await client.login(homey_email_address, homey_password)
    await client.connect_to_cloud_remote()

    devices = await client.get_devices()
    drivers = await client.get_drivers()

    for device in devices:
        driver = next(driver for driver in drivers if driver.id == device.driver_id)

        print(f"- {device.name} ({driver.owner_name}, {driver.name}, {device.id}){':' if len(device.capabilities) > 0 else ''}")

        for capability in device.capabilities:
            print(f"    - {capability.title} ({capability.id}, {capability.value_type}) => {capability.value}")

            if capability.value and capability.gettable and capability.settable:
                await client.set_device_capability_value(device.id, capability.id, capability.value)

asyncio.run(main())
