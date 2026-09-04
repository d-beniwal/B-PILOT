#!/bin/bash

# starter_scripts/mpe.sh
# ---------------------------------------------------------------------------
# Equivalent to blueskyStarter.sh, but for the GUI's EMBEDDED kernel launch:
# it does the same environment activation and experiment recording, then starts
# a *connectable Jupyter ipykernel* (in a named screen session, at a connection
# file the GUI chose) instead of an interactive IPython REPL — so the GUI can
# attach to it and the transcript/queue features work.
#
# The kernel is started with --profile=bluesky, so the profile's startup script
# (__start_bluesky_instrument__.py) runs on kernel start and does
# `from instrument.collection import *` — i.e. the same activation the console
# path performs.  (If the profile startup does not auto-run in your ipykernel
# version, the GUI's "Load Bluesky" button remains as a fallback.)
#
# Usage:
#   mpe.sh <dm_experiment> <setup_file> <connection_file> <screen_session>
#
# Lives in the GUI bundle dir (<mpe_bluesky>/gui/); see .context/DECISIONS.md.
# ---------------------------------------------------------------------------

# Python environment name: BLUESKY_CONDA_ENV, else DEFAULT_ENV
DEFAULT_ENV=bluesky_2024_2
export ENV_NAME="${BLUESKY_CONDA_ENV:-${DEFAULT_ENV}}"
export IPYTHON_PROFILE=bluesky
export IPYTHONDIR="${HOME}/.ipython"

# where the instrument code + user_defaults live (blueskyStarter.sh uses ~/bluesky)
BLUESKY_DIR="${BLUESKY_DIR:-${HOME}/bluesky}"


# ── environment activation (copied from blueskyStarter.sh) ──────────────────

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

# ── record the experiment for Bluesky to read (like blueskyStarter.sh) ──────
# SAFETY: only overwrite when a NON-EMPTY value was passed.  These files drive
# the beamline's session-log paths (instrument/session_logs.py reads
# dm_experiment.txt → ~/new_data/<dm_exp>/.logs).  Launching the GUI without an
# experiment configured must NOT clobber the live experiment recorded here, or a
# running session's logs would be misdirected.  Configure the experiment in the
# GUI (Preferences) before launching to set it.
DEFAULTS_DIR="${BLUESKY_DIR}/user/user_defaults"
if [ -d "${DEFAULTS_DIR}" ]; then
    if [ -n "${DM_EXPERIMENT}" ]; then
        echo "${DM_EXPERIMENT}" > "${DEFAULTS_DIR}/dm_experiment.txt"
        echo "==> recorded experiment '${DM_EXPERIMENT}'"
    else
        echo "==> no experiment passed — keeping existing dm_experiment.txt ('$(cat "${DEFAULTS_DIR}/dm_experiment.txt" 2>/dev/null)')"
    fi
    if [ -n "${SETUP_FILE}" ]; then
        echo "${SETUP_FILE}" > "${DEFAULTS_DIR}/setup_file.txt"
        echo "==> recorded setup '${SETUP_FILE}'"
    else
        echo "==> no setup file passed — keeping existing setup_file.txt ('$(cat "${DEFAULTS_DIR}/setup_file.txt" 2>/dev/null)')"
    fi
else
    echo "WARNING: ${DEFAULTS_DIR} not found — skipping experiment file write."
fi

# ── activate the environment ────────────────────────────────────────────────
pick_environment_executable

# ── start the KERNEL (not a REPL) in a detached screen session ──────────────
# The GUI attaches to CONNECTION_FILE.  screen -dmS inherits the activated env.
if [ -z "${CONNECTION_FILE}" ] || [ -z "${SCREEN_NAME}" ]; then
    echo "ERROR: connection file and screen session name are required."
    exit 2
fi

# ── startup commands run inside the kernel (--IPKernelApp.exec_lines) ───────
# The collection import is done HERE rather than through B-PILOT's
# `bluesky_startup` profile setting (now blank for the MPE profiles). It runs
# in the kernel's IPython USER namespace at init, so every name it binds is
# visible to later console cells -- `python -c "from x import *"` ahead of the
# launcher does NOT work for this, IPython builds a fresh `__main__` for the
# shell and discards the launcher's namespace (verified).
#
# --profile=bluesky is still passed and its startup files still run; this line
# makes the import explicit and deterministic rather than depending on whether
# the profile startup auto-runs under ipykernel (a long-standing open question,
# see .context/STATE.md). Importing twice is harmless -- sys.modules caches the
# module, so the second import only rebinds names and re-connects nothing.
#
# ⚠ TRADE-OFF, deliberate: exec_lines run before any client can connect, so
# their output is published to iopub with nobody subscribed and is DISCARDED --
# it reaches neither B-PILOT's console nor this screen session. A failing
# import leaves the kernel up and apparently healthy. If Bluesky names are
# missing, re-run the import by hand in the B-PILOT console to see the real
# traceback.
# Verified against ipykernel 7.3.0.
#
# CONNECTS TO HARDWARE / EPICS -- this is the collection import.
EXEC_LINES='["from instrument.collection import *"]'

echo "==> starting ipykernel in screen '${SCREEN_NAME}' at ${CONNECTION_FILE}"
echo "==> kernel startup lines: ${EXEC_LINES}"
screen -dmS "${SCREEN_NAME}" bash -c \
    "python -X frozen_modules=off -m ipykernel_launcher -f '${CONNECTION_FILE}' --profile=${IPYTHON_PROFILE} --ipython-dir=${IPYTHONDIR} --IPKernelApp.exec_lines='${EXEC_LINES}'"
