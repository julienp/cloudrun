from dataclasses import dataclass
from typing import Optional

import pulumi
import pulumi_command as command
import pulumi_docker_build as docker_build
from pulumi_gcp import artifactregistry, cloudrun
from pulumi_gcp import config as gcp_config


@dataclass
class ServiceArgs:
    app_path: Optional[pulumi.Input[str]] = "./app"
    """The path to the application source code."""
    image_name: Optional[pulumi.Input[str]] = "image"
    """The name of the Docker image."""
    container_port: Optional[pulumi.Input[int]] = 8080
    """The port the container listens on."""
    cpu: Optional[pulumi.Input[int]] = 1
    """The CPU limit for the container."""
    memory: Optional[pulumi.Input[str]] = "1Gi"
    """The memory limit for the container."""
    concurrency: Optional[pulumi.Input[int]] = 3
    """The number of concurrent containers to run."""


class Service(pulumi.ComponentResource):
    """
    Service is a component that builds and pushes a Docker image to Artifact
    Registry and deploys it to Cloud Run.
    """

    url: pulumi.Output[Optional[str]]
    """The URL of the deployed service."""
    image_ref: pulumi.Output[str]
    """The Docker image of the service"""

    def __init__(
        self,
        name: str,
        args: ServiceArgs,
        opts: Optional[pulumi.ResourceOptions] = None,
    ):
        super().__init__("cloudrun:index:Service", name, {}, opts)
        if not gcp_config.project:
            raise ValueError("Missing required configuration value `gcp:project`")
        if not gcp_config.region:
            raise ValueError("Missing required configuration value `gcp:region`")

        self.artifact_registry_repo = artifactregistry.Repository(
            f"{name}-artifact-repo",
            format="DOCKER",
            repository_id=f"{name}-repo",
            opts=pulumi.ResourceOptions(parent=self),
        )

        docker_token = command.local.run_output(
            command="gcloud auth print-access-token --format=text",
            logging=command.local.Logging.NONE,
        ).stdout.apply(lambda x: x.split(":")[1].strip())

        self.image = docker_build.Image(
            f"{name}-image",
            tags=[
                pulumi.Output.concat(
                    gcp_config.region,
                    "-docker.pkg.dev/",
                    gcp_config.project,
                    "/",
                    self.artifact_registry_repo.name,
                    "/",
                    args.image_name or "image",
                    ":latest",
                ),
            ],
            registries=[
                {
                    "address": pulumi.Output.concat(
                        "https://", gcp_config.region, "-docker.pkg.dev"
                    ),
                    "password": docker_token,
                    "username": "oauth2accesstoken",
                }
            ],
            context=docker_build.BuildContextArgs(
                location=args.app_path or "./app",
            ),
            platforms=[docker_build.Platform.LINUX_AMD64],
            push=True,  # TODO: this is required, but the argument types don't reflect that, bug?
            opts=pulumi.ResourceOptions(parent=self),
        )

        # Create a Cloud Run service definition.
        self.service = cloudrun.Service(
            f"{name}-service",
            location=gcp_config.region,
            template={
                "spec": {
                    "containers": [
                        {
                            "image": self.image.ref,
                            "resources": {
                                "limits": {
                                    "memory": args.memory or "1Gi",
                                    "cpu": pulumi.Output.from_input(
                                        args.cpu or 1
                                    ).apply(lambda x: str(x)),
                                },
                            },
                            "ports": [
                                {"container_port": args.container_port or 8080},
                            ],
                            "envs": [
                                {
                                    "name": "FLASK_RUN_PORT",
                                    "value": pulumi.Output.from_input(
                                        args.container_port or 8080
                                    ).apply(lambda x: str(int(x))),
                                },
                            ],
                        },
                    ],
                    "container_concurrency": args.concurrency or 3,
                },
            },
            opts=pulumi.ResourceOptions(parent=self),
        )

        # Create an IAM member to make the service publicly accessible.
        self.invoker = cloudrun.IamMember(
            f"{name}-invoker",
            location=gcp_config.region,
            service=self.service.name,
            role="roles/run.invoker",
            member="allUsers",
            opts=pulumi.ResourceOptions(parent=self),
        )

        self.url = self.service.statuses.apply(lambda statuses: statuses[0].url)
        self.image_ref = self.image.ref
        self.register_outputs({})
