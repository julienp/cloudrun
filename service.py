from typing import Optional, TypedDict

import pulumi
import pulumi_command as command
import pulumi_docker_build as docker_build
from pulumi_gcp import artifactregistry, cloudrun
from pulumi_gcp import config as gcp_config


class ServiceArgs(TypedDict):
    app_path: Optional[pulumi.Input[str]]
    """The path to the application source code."""
    image_name: Optional[pulumi.Input[str]]
    """The name of the Docker image."""
    container_port: Optional[pulumi.Input[int]]
    """The port the container listens on."""
    cpu: Optional[pulumi.Input[int]]
    """The CPU limit for the container."""
    memory: Optional[pulumi.Input[str]]
    """The memory limit for the container."""
    concurrency: Optional[pulumi.Input[int]]
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
                _docker_tag(
                    self.artifact_registry_repo, args.get("image_name") or "image"
                )
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
                location=args.get("app_path") or "./app",
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
                                    "memory": args.get("memory") or "1Gi",
                                    "cpu": pulumi.Output.from_input(
                                        args.get("cpu") or 1
                                    ).apply(lambda x: str(x)),
                                },
                            },
                            "ports": [
                                {"container_port": args.get("container_port") or 8080},
                            ],
                            "envs": [
                                {
                                    "name": "FLASK_RUN_PORT",
                                    "value": pulumi.Output.from_input(
                                        args.get("container_port") or 8080
                                    ).apply(lambda x: str(int(x))),
                                },
                            ],
                        },
                    ],
                    "container_concurrency": args.get("concurrency") or 3,
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
        # By registering the outputs on which the component depends, we ensure
        # that the Pulumi CLI will wait for all the outputs to be created before
        # considering the component itself to have been created.
        self.register_outputs(
            {
                "invoker": self.invoker,
                "service": self.service,
                "image": self.image,
                "artifactRegistryRepo": self.artifact_registry_repo,
            }
        )


def _docker_tag(
    repo: artifactregistry.Repository, image_name: pulumi.Input[str]
) -> pulumi.Output[str]:
    return pulumi.Output.concat(
        repo.location,
        "-docker.pkg.dev/",
        repo.project,
        "/",
        repo.name,
        "/",
        image_name,
        ":latest",
    )
