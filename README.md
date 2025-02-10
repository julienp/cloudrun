# Python Components Example

This is an example component written in Python using type annotations to define the schema.

The `Service` component builds and pushes a Docker image to Artifact Registry and deploys it to GCP Cloud Run.

To use the component in a Pulumi project, run:

```bash
pulumi package add https://github.com/julienp/cloudrun@v2.1.0
```
