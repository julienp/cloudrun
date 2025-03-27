from pulumi.provider.experimental import component_provider_host

from service import Service

if __name__ == "__main__":
    component_provider_host(name="cloudrun", components=[Service])
