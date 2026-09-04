#!/bin/bash

# starter_scripts/bits_3idc.sh
# ---------------------------------------------------------------------------
# BITS variant of mpe.sh, for an APS **BITS** instrument
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
#     ipykernel and runs `from id3c.startup import *` itself, via
#     --IPKernelApp.exec_lines (see the kernel-launch section at the bottom).
#  3. **nest_asyncio is applied here too**, ahead of that import — same
#     section.
#
# The MPE starter's dm_experiment/setup_file bookkeeping is deliberately NOT
# reproduced: those files drive `instrument/session_logs.py`, which does not
# exist in a BITS instrument.  The arguments are still accepted (and echoed)
# so the calling convention stays identical to the MPE starter's.
#
# Usage:
#   bits_3idc.sh <dm_experiment> <setup_file> <connection_file> <screen_session>
#
# Lives in the GUI bundle's starter_scripts/ dir, alongside mpe.sh.
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
# No --profile: this starter runs the instrument import itself via
# --IPKernelApp.exec_lines (see below).
if [ -z "${CONNECTION_FILE}" ] || [ -z "${SCREEN_NAME}" ]; then
    echo "ERROR: connection file and screen session name are required."
    exit 2
fi

# ── startup commands run inside the kernel (--IPKernelApp.exec_lines) ───────
# These run in the kernel's IPython USER namespace at init, so the names they
# bind are visible to every later console cell. `python -c "from x import *"`
# ahead of launch_new_instance() does NOT work for this: IPython builds a fresh
# `__main__` for the shell and the launcher's own namespace is discarded
# (verified -- the names silently vanish). exec_lines is the mechanism.
#
# 1. nest_asyncio -- apsbits' `make_devices()` is synchronous and calls
#    `asyncio.run()`, illegal inside the event loop ipykernel already runs in
#    its main thread. make_devices swallows the RuntimeError and only logs it,
#    so the session comes up with an EMPTY oregistry and dies much later on the
#    first `oregistry[...]` lookup (`ComponentNotFound: 'eiger2'`) -- three
#    steps downstream of the real cause. Applying nest_asyncio makes the loop
#    reentrant. 3-ID-C's own start_3idc_bluesky.sh never needed it: terminal
#    IPython has no running loop during a plain statement. Must precede (2).
# 2. The instrument import itself, replacing B-PILOT's `bluesky_startup`
#    profile setting (now blank for this profile).
#
# Both live here rather than in the profile config because they describe how
# this kernel is brought up, not a beamline preference -- and because a
# starter change actually ships with a `git pull`, whereas a profile change
# never reaches a workstation that already has an active_config.json (see
# .context/DECISIONS.md, 2026-09-03 5th entry).
#
# ⚠ TRADE-OFF, deliberate: exec_lines run before any client can connect, so
# their output is published to iopub with nobody subscribed and is DISCARDED --
# it reaches neither B-PILOT's console nor this screen session. A failing
# import leaves the kernel up and apparently healthy. If devices are missing,
# re-run the import by hand in the B-PILOT console to see the real traceback.
# Verified against ipykernel 7.3.0.
if python -c "import nest_asyncio" 2>/dev/null; then
    EXEC_LINES='["import nest_asyncio; nest_asyncio.apply()", "from id3c.startup import *"]'
else
    EXEC_LINES='["from id3c.startup import *"]'
    echo "==> WARNING: 'nest_asyncio' is not installed in env '${ENV_NAME}'."
    echo "    apsbits' device loading will fail and the session will start"
    echo "    with NO devices (surfacing later as ComponentNotFound)."
    echo "    Fix:  conda activate ${ENV_NAME} && pip install nest_asyncio"
fi

echo "==> starting ipykernel in screen '${SCREEN_NAME}' at ${CONNECTION_FILE}"
echo "==> kernel startup lines: ${EXEC_LINES}"
screen -dmS "${SCREEN_NAME}" bash -c \
    "python -X frozen_modules=off -m ipykernel_launcher -f '${CONNECTION_FILE}' --IPKernelApp.exec_lines='${EXEC_LINES}'"
