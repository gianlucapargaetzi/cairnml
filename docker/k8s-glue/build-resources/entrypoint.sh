#!/bin/bash -x

if [ -z "$CAIRNML_WEB_HOST" ] || [ -z "$CAIRNML_API_HOST" ]; then
    CAIRNML_HOST_IP=${CAIRNML_HOST_IP:-${TRAINS_HOST_IP:-$(curl -s https://ifconfig.me/ip)}}
fi

export CAIRNML_WEB_HOST=${CAIRNML_WEB_HOST:-${TRAINS_WEB_HOST:-"http://$CAIRNML_HOST_IP:8080"}}
export CAIRNML_API_HOST=${CAIRNML_API_HOST:-${TRAINS_API_HOST:-"http://$CAIRNML_HOST_IP:8008"}}

echo $CAIRNML_WEB_HOST $CAIRNML_API_HOST 1>&2

if [ -z "$CAIRNML_AGENT_NO_UPDATE" ]; then
  if [ -n "$CAIRNML_AGENT_UPDATE_REPO" ]; then
    python3 -m pip install -q -U "$CAIRNML_AGENT_UPDATE_REPO"
  else
    python3 -m pip install -q -U "cairnml-agent${CAIRNML_AGENT_UPDATE_VERSION:-$TRAINS_AGENT_UPDATE_VERSION}"
  fi
fi

QUEUE=${K8S_GLUE_QUEUE:-k8s_glue}
MAX_PODS=${K8S_GLUE_MAX_PODS:-2}
EXTRA_ARGS=${K8S_GLUE_EXTRA_ARGS:-}

# shellcheck disable=SC2129
echo "api.api_server: ${CAIRNML_API_HOST}" >> ~/cairnml.conf
echo "api.web_server: ${CAIRNML_WEB_HOST}" >> ~/cairnml.conf

./provider_entrypoint.sh

if [[ -z "${K8S_GLUE_MAX_PODS}" ]]
then
  python3 k8s_glue_example.py --queue ${QUEUE} ${EXTRA_ARGS}
else
  python3 k8s_glue_example.py --queue ${QUEUE} --max-pods ${MAX_PODS} ${EXTRA_ARGS}
fi
