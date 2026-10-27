#!/bin/bash +x

if [ -n "$SHUTDOWN_IF_NO_ACCESS_KEY" ] && [ -z "$CAIRNML_AUTH_TOKEN" ] && [ ! -f "$HOME/.cairnml/device.key" ]; then
  echo "This agent is not enrolled (no device key and no CAIRNML_AUTH_TOKEN), service will not be started"
  exit 0
fi

if [ -z "$CAIRNML_WEB_HOST" ] || [ -z "$CAIRNML_API_HOST" ]; then
    CAIRNML_HOST_IP=${CAIRNML_HOST_IP:-${TRAINS_HOST_IP:-$(curl -s https://ifconfig.me/ip)}}
fi

export CAIRNML_WEB_HOST=${CAIRNML_WEB_HOST:-${TRAINS_WEB_HOST:-"http://$CAIRNML_HOST_IP/cairnml"}}
export CAIRNML_API_HOST=${CAIRNML_API_HOST:-${TRAINS_API_HOST:-"http://$CAIRNML_HOST_IP/cairnml/api"}}

echo $CAIRNML_WEB_HOST $CAIRNML_API_HOST 1>&2

if [[ "$CAIRNML_AGENT_UPDATE_VERSION" =~ ^[0-9]{1,3}\.[0-9]{1,3}(\.[0-9]{1,3}([a-zA-Z]{1,3}[0-9]{1,3})?)?$ ]]
then
    CAIRNML_AGENT_UPDATE_VERSION="==$CAIRNML_AGENT_UPDATE_VERSION"
fi

DAEMON_OPTIONS=${CAIRNML_AGENT_DAEMON_OPTIONS:---services-mode --create-queue}
QUEUES=${CAIRNML_AGENT_QUEUES:-services}

if [ -z "$CAIRNML_AGENT_NO_UPDATE" ]; then
  if [ -n "$CAIRNML_AGENT_UPDATE_REPO" ]; then
    python3 -m pip install -q -U $CAIRNML_AGENT_UPDATE_REPO
  else
    python3 -m pip install -q -U "cairnml-agent${CAIRNML_AGENT_UPDATE_VERSION:-$TRAINS_AGENT_UPDATE_VERSION}"
  fi
fi

DOCKER_ARGS="--docker \"${CAIRNML_AGENT_DEFAULT_BASE_DOCKER:-$TRAINS_AGENT_DEFAULT_BASE_DOCKER}\""

if [ -n "$CAIRNML_AGENT_NO_DOCKER" ]; then
  DOCKER_ARGS=""
fi

cairnml-agent daemon $DAEMON_OPTIONS --queue $QUEUES $DOCKER_ARGS --cpu-only ${CAIRNML_AGENT_EXTRA_ARGS:-$TRAINS_AGENT_EXTRA_ARGS}
