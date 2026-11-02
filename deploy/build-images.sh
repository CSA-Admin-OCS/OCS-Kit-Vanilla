#!/usr/bin/env bash
# Build all five images from the sibling repo folders and print the push commands.
#
#   ./build-images.sh <version> [--platform linux/amd64,linux/arm64] [--push]
#
# Tags: wolfjack20/ocs-in-kit:{flask,spring,flask-runner,spring-runner,socket}-<version> and -latest.
# --push pushes after building (you must be logged in). Several platforms need --push: a multi-arch
# image cannot be loaded into the local docker. Repo folders: FLASK_REPO and SPRING_REPO override.
set -euo pipefail

REPO="wolfjack20/ocs-in-kit"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FLASK_REPO="${FLASK_REPO:-$HERE/../../OCS-Kit-Flask}"
SPRING_REPO="${SPRING_REPO:-$HERE/../../OCS-Kit-Spring}"

usage() { sed -n '2,8p' "$0" | sed 's/^# \{0,1\}//'; exit "${1:-2}"; }

VERSION="" PLATFORM="" PUSH=0
while [ $# -gt 0 ]; do
    case "$1" in
        --push) PUSH=1 ;;
        --platform) [ $# -ge 2 ] || usage; PLATFORM="$2"; shift ;;
        --platform=*) PLATFORM="${1#*=}" ;;
        -h|--help) usage 0 ;;
        -*) echo "Unknown option: $1" >&2; usage ;;
        *) [ -z "$VERSION" ] || usage; VERSION="$1" ;;
    esac
    shift
done
[ -n "$VERSION" ] || usage
VERSION="${VERSION#v}"
if ! [[ "$VERSION" =~ ^[A-Za-z0-9_][A-Za-z0-9_.-]{0,100}$ ]] || [ "$VERSION" = "latest" ]; then
    echo "Not a usable version: $VERSION" >&2
    exit 2
fi
for dir in "$FLASK_REPO" "$SPRING_REPO"; do
    [ -f "$dir/Dockerfile" ] || { echo "No Dockerfile in $dir (set FLASK_REPO / SPRING_REPO)" >&2; exit 1; }
done
if [[ "$PLATFORM" == *,* ]] && [ "$PUSH" -eq 0 ]; then
    echo "Note: a multi-platform build stays in the build cache until you run it with --push." >&2
fi

# The bundle carries copies of these so it works on a server without the repos.
drift() { cmp -s "$1" "$HERE/templates/$2" || echo "WARNING: templates/$2 differs from $1; refresh the copy." >&2; }
drift "$FLASK_REPO/.env.example" flask.env.example
drift "$SPRING_REPO/.env.example" spring.env.example
drift "$FLASK_REPO/deploy/nginx.conf.template" flask-nginx.conf.template
drift "$FLASK_REPO/deploy/socket-nginx.conf.template" socket-nginx.conf.template
drift "$SPRING_REPO/deploy/nginx.conf.template" spring-nginx.conf.template

IMAGES=(flask spring flask-runner spring-runner socket)
context() {
    case "$1" in
        flask) echo "$FLASK_REPO" ;;
        flask-runner) echo "$FLASK_REPO/runner" ;;
        socket) echo "$FLASK_REPO/socket" ;;
        spring) echo "$SPRING_REPO" ;;
        spring-runner) echo "$SPRING_REPO/runner" ;;
    esac
}

for name in "${IMAGES[@]}"; do
    echo "==> $REPO:$name-$VERSION"
    tags=(-t "$REPO:$name-$VERSION" -t "$REPO:$name-latest" --label "org.opencontainers.image.version=$VERSION")
    if [ -n "$PLATFORM" ]; then
        out=()
        if [ "$PUSH" -eq 1 ]; then out=(--push); elif [[ "$PLATFORM" != *,* ]]; then out=(--load); fi
        docker buildx build --platform "$PLATFORM" "${tags[@]}" "${out[@]}" "$(context "$name")"
    else
        docker build "${tags[@]}" "$(context "$name")"
    fi
done

if [ "$PUSH" -eq 1 ] && [ -z "$PLATFORM" ]; then
    for name in "${IMAGES[@]}"; do
        docker push "$REPO:$name-$VERSION"
        docker push "$REPO:$name-latest"
    done
fi

echo
if [ "$PUSH" -eq 1 ]; then
    echo "Pushed version $VERSION."
elif [[ "$PLATFORM" == *,* ]]; then
    echo "Built for $PLATFORM. To publish: docker login -u wolfjack20 && $0 $VERSION --platform $PLATFORM --push"
else
    echo "Built. To publish, run:"
    echo "  docker login -u wolfjack20"
    for name in "${IMAGES[@]}"; do
        echo "  docker push $REPO:$name-$VERSION"
        echo "  docker push $REPO:$name-latest"
    done
fi
