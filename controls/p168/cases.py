"""Frozen case set of ACTINV-P168 (protocols/ACTINV-P168_PROTOCOL.md). Changing anything here changes the protocol."""

ORDERS = (16, 48)  # options.cram_order values, each case is run once per order
TIMEOUT_S = 1800  # per ACTINV run, killed and reaped at the limit
OUTPUTS = ["inventory"]  # options.outputs of every run
ACTIVITY_FRACTION = 1e-6  # M2 covers nuclides whose reference activity is at least this share of the total
THRESHOLDS = {"M1": 1e-10, "M2": 1e-6, "M3": 1e-6}  # "holds" needs every metric at or below its threshold at every step
TIME_TOLERANCE = 1e-9  # relative agreement of the reported t_s with the reference cumulative time

# name: reference file stem; mode: options.mode; pulses: pulses actually scheduled for pulse-train cases (None otherwise);
# trace: M3 (relative error on every reference nuclide) applies
CASES = [
    {"name": "1a_u238_chain_pure_parent", "mode": "coupled", "pulses": None, "trace": False},
    {"name": "1b_u238_chain_equilibrated", "mode": "coupled", "pulses": None, "trace": False},
    {"name": "2_very_short_times", "mode": "coupled", "pulses": None, "trace": False},
    {"name": "3_co59_flux1e14", "mode": "coupled", "pulses": None, "trace": False},
    {"name": "3_co59_flux1e15", "mode": "coupled", "pulses": None, "trace": False},
    {"name": "3_co59_flux1e16", "mode": "coupled", "pulses": None, "trace": False},
    {"name": "4_pulse_train_0p1s_1e4", "mode": "coupled", "pulses": 10_000, "trace": False},
    {"name": "4_pulse_train_0p1s_1e5", "mode": "coupled", "pulses": 100_000, "trace": False},
    {"name": "4_pulse_train_0p1s_1e6", "mode": "coupled", "pulses": 1_000, "trace": False},
    {"name": "4_pulse_train_1s_1e6", "mode": "coupled", "pulses": 1_000, "trace": False},
    {"name": "5a_loop_weak", "mode": "coupled", "pulses": None, "trace": False},
    {"name": "5a_loop_strong", "mode": "coupled", "pulses": None, "trace": False},
    {"name": "5b_isomer_loop", "mode": "coupled", "pulses": None, "trace": False},
    {"name": "5b_isomer_loop_strong", "mode": "coupled", "pulses": None, "trace": False},
    {"name": "5c_defective_capture_decay", "mode": "coupled", "pulses": None, "trace": False},
    {"name": "5d_defective_decay_triple", "mode": "coupled", "pulses": None, "trace": False},
    {"name": "6a_dynamic_range_coupled", "mode": "coupled", "pulses": None, "trace": False},
    {"name": "6b_dynamic_range_trace", "mode": "trace", "pulses": None, "trace": True},
    {"name": "7_tungsten_burnup", "mode": "coupled", "pulses": None, "trace": False},
]
BY_NAME = {case["name"]: case for case in CASES}
