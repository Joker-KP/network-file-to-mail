# Publish to Docker Hub and deploy on Synology

The publishing script builds this project's Dockerfile and pushes the image to `macjoker/scan-to-mail:latest`. The NAS then pulls that image; it needs only its private YAML configuration and persistent scan/state directories, with no source checkout, Compose project, or image archive to transfer.

## 1. Prepare Docker Hub access

Create the `scan-to-mail` repository under the `macjoker` namespace if it does not already exist, and choose its visibility. A public repository allows anyone to pull; a private repository requires authorized access. See Docker's [repository setup instructions](https://docs.docker.com/docker-hub/repos/create/).

On the build machine, authenticate with an account that can push to that repository:

```bash
docker login --username macjoker
```

Enter your Docker Hub credentials or access token at the prompt. The script uses Docker's existing login and does not store credentials in the project. See the [Docker login reference](https://docs.docker.com/reference/cli/docker/login/).

## 2. Build and publish

From the repository root:

```bash
./scripts/publish-dockerhub.sh
```

This runs:

```bash
docker build -t macjoker/scan-to-mail:latest --push .
```

The script finds the repository root even when invoked from another working directory. It returns Docker's exit status, so authentication, build, and push failures are reported as failures. Run `./scripts/publish-dockerhub.sh --help` for usage.

Additional Docker build options are forwarded unchanged. Build for the NAS CPU architecture when it differs from the build machine, for example:

```bash
./scripts/publish-dockerhub.sh --platform linux/amd64
# For an ARM64 NAS instead:
./scripts/publish-dockerhub.sh --platform linux/arm64
```

Each command above replaces `latest` with an image for the selected platform. For multiple architectures under one tag, use a builder supporting those platforms and a combined platform list instead. Cross-platform builds require a suitable builder. Docker documents [`--platform`, `--tag`, and `--push` here](https://docs.docker.com/reference/cli/docker/buildx/build/).

To publish a version tag alongside `latest`:

```bash
./scripts/publish-dockerhub.sh --tag macjoker/scan-to-mail:1.0.0
```

Use a tag for the release you are publishing. `latest` moves with each push; a version tag lets the NAS select a specific release, provided you do not overwrite that tag later. Publishing does not update running containers automatically.

The image contains application code and dependencies. The `config/` directory, `.env` files, scan data, and SQLite state are excluded by `.dockerignore`. Keep the SMTP password in the NAS's private runtime YAML, outside the image.

## 3. Pull and configure on Synology

Prepare the directories, private YAML, permissions, environment values, and volume mappings described in the [Synology setup guide](../README.md#2-prepare-nas-directories-and-yaml).

Pull the image in Container Manager using `macjoker/scan-to-mail:latest`, or over SSH:

```bash
docker pull macjoker/scan-to-mail:latest
```

If the repository is private, configure Docker Hub authentication in Container Manager, or run `docker login --username macjoker` on the NAS before pulling over SSH. CLI login and Container Manager registry credentials are configured separately.

Create the container with the image's default entrypoint and an empty command. Set `PUID` and `PGID` to the NAS identity that can access the mounted files; the default `1026:100` is only an example. Keep `SCANMAIL_CONFIG=/config/config.yaml` and `SCANMAIL_STATE_DIR=/state` when using the standard mounts. Set `TZ` as needed. All SMTP settings remain in YAML.

Equivalent SSH validation and startup commands, using example NAS paths and identity:

```bash
docker run --rm \
  -e PUID=1026 -e PGID=100 \
  --mount type=bind,src=/volume1/docker/scan-to-email/config,dst=/config,readonly \
  macjoker/scan-to-mail:latest --check-config

docker run -d \
  --name scan-to-email \
  --restart unless-stopped \
  --init \
  --read-only \
  --tmpfs /tmp:rw,nosuid,nodev,size=16m \
  --security-opt no-new-privileges:true \
  --log-opt max-size=10m --log-opt max-file=3 \
  -e PUID=1026 -e PGID=100 -e TZ=Europe/Warsaw \
  --mount type=bind,src=/volume1/ScanToMail,dst=/data \
  --mount type=bind,src=/volume1/docker/scan-to-email/state,dst=/state \
  --mount type=bind,src=/volume1/docker/scan-to-email/config,dst=/config,readonly \
  macjoker/scan-to-mail:latest

docker logs -f --tail=100 scan-to-email
```

No port mappings are needed. For a versioned deployment, replace `latest` in the pull and run commands with the published version tag. Use `sudo` for Docker commands if required by your NAS permissions; authenticate with the same Docker user context used for pulling.

## 4. Update an existing container

Pull the newly published image, then recreate the container with its existing environment settings and mounts. Over SSH, for the standard container name:

```bash
docker pull macjoker/scan-to-mail:latest
docker stop scan-to-email
docker rm scan-to-email
```

Then repeat the startup command above with your actual environment settings and mount paths. In Container Manager, pull the image and recreate the container with those same settings. Keep the NAS scan directories, private YAML, and especially the persistent state directory. Pulling an image or restarting an existing container alone does not switch it to a new image.

For a rollback, pull a previously published version tag and recreate the container using it and the same mounts. Route IDs and inbox paths should stay unchanged so pending archive retries retain their ledger identity.
