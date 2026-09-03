#!/bin/bash

# embedded_kernel_starter_3idc.sh
# ---------------------------------------------------------------------------
# BITS variant of embedded_kernel_starter.sh, for an APS **BITS** instrument
# (3-ID-C / the `3idc-bits` checkout, package `id3c`) rather than the
# `mpe_bluesky` tree.
#
# Two things differ from the MPE starter:
#
#  1. **Environment.**  Default conda env is `3idc-bits`, not `bluesky_2024_2`
#     (override with BLUESKY_CONDA_ENV).
#  2. **No IPython profile.**  MPE relies on an `ipython --profile=bluesky`
#     whose startup script does the collection import.  3-ID-C has no such
#     profile — `start_3idc_bluesky.sh` just runs
#     `ipython -i -c "from id3c.startup import *"`.  So this starts a plain
#     ipykernel and lets B-PILOT's own `bluesky_startup` profile setting send
#     `from id3c.startup import *` once the console connects.  One code path
#     for the import, visible in the console, instead of two.
#
# The MPE starter's dm_experiment/setup_file bookkeeping is deliberately NOT
# reproduced: those files drive `instrument/session_logs.py`, which does not
# exist in a BITS instrument.  The arguments are still accepted (and echoed)
# so the calling convention stays identical to the MPE starter's.
#
# Usage:
#   embedded_kernel_starter_3idc.sh <dm_experiment> <setup_file> <connection_file> <screen_session>
#
# Lives in the GUI bundle dir, alongside embedded_kernel_starter.sh.
# ---------------------------------------------------------------------------

DEFAULT_ENV=3idc-bits
export ENV_NAME="${BLUESKY_CONDA_ENV:-${DEFAULT_ENV}}"


# ── environment activation (same probe order as the MPE starter) ────────────

pick () {  # activate ENV_NAME using (conda) from given arg
    ARG="${1}"
    if [ "${ARG}" == "" ]; then
        return 1
    fi
    if [ -d "${ARG}" ]; then
        pick "${ARG}/bin/conda"
        if [ "${cmd_base}" != "" ]; then
            return 0
        fi
        return 1
    fi
    CMD=$(which ${ARG})
    if [ "${CMD}" == "" ]; then
        return 1
    fi
    if [ -x "${CMD}" ]; then
        match_env_name=$( \
            ${CMD} env list \
            | grep "^[ ]*${ENV_NAME} " \
            | awk '{print $1}' \
        )
        if [ "${match_env_name}" != "" ]; then
            cmd_base=$(basename "${CMD}")
            case "${cmd_base}" in
                conda | mamba)
                    source "$(dirname ${CMD})/activate" base
                    "${cmd_base}" activate "${ENV_NAME}"
                    return 0
                    ;;
                *)
                    return 1
                    ;;
            esac
        fi
    fi
    return 2
}

pick_environment_executable () {  # Activate the environment (first hit wins)
    pick "/APSshare/miniconda/x86_64" \
    || pick "${HOME}" \
    || pick "conda" \
    || pick "/opt/miniconda3" \
    || pick "${HOME}/Apps/miniconda" \
    || pick "${HOME}/anaconda3" \
    || pick "${HOME}/Apps/anaconda"

    echo "==> CONDA_PREFIX=${CONDA_PREFIX}"
    if [ "${cmd_base}" != "" ]; then
        echo "$(which python) -- $(python --version)"
        return 0
    fi
    echo "Could not activate environment: '${ENV_NAME}' (continuing with current python)"
    return 3
}


# ── arguments ───────────────────────────────────────────────────────────────

if [ "$#" -lt 2 ]; then
    echo "Usage: $0 <dm_experiment> <setup_file> <connection_file> <screen_session>"
    exit 1
fi

DM_EXPERIMENT="$1"
SETUP_FILE="$2"
CONNECTION_FILE="$3"
SCREEN_NAME="$4"

# Recorded for the operator's benefit only -- a BITS instrument has no
# user_defaults/dm_experiment.txt for these to be written into.
echo "==> experiment '${DM_EXPERIMENT:-<unset>}', setup '${SETUP_FILE:-<unset>}'"

# ── activate the environment ────────────────────────────────────────────────
pick_environment_executable

# ── start the KERNEL (not a REPL) in a detached screen session ──────────────
# No --profile: B-PILOT sends `from id3c.startup import *` itself (see the
# profile's `bluesky_startup` setting) once it connects to this kernel.
if [ -z "${CONNECTION_FILE}" ] || [ -z "${SCREEN_NAME}" ]; then
    echo "ERROR: connection file and screen session name are required."
    exit 2
fi

echo "==> starting ipykernel in screen '${SCREEN_NAME}' at ${CONNECTION_FILE}"
screen -dmS "${SCREEN_NAME}" bash -c \
    "python -X frozen_modules=off -m ipykernel_launcher -f '${CONNECTION_FILE}'"
