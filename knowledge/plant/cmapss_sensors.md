# NASA C-MAPSS: what the sensors mean

Source: A. Saxena, K. Goebel, D. Simon, N. Eklund, "Damage Propagation Modeling for Aircraft Engine Run-to-Failure Simulation", PHM 2008 (NASA Ames Prognostics Data Repository). The data is simulated, not measured on a real fleet.

## Sensor list (FD001)

Sensor 2 (T24) is the total temperature at the low pressure compressor (LPC) outlet. It rises as the engine wears.

Sensor 3 (T30) is the total temperature at the high pressure compressor (HPC) outlet. It rises as the HPC degrades.

Sensor 4 (T50) is the total temperature at the low pressure turbine (LPT) outlet. It rises as the engine wears.

Sensor 7 (P30) is the total pressure at the HPC outlet. It falls as the HPC degrades.

Sensor 11 (Ps30) is the static pressure at the HPC outlet. It rises with degradation.

Sensor 12 (phi) is the ratio of fuel flow to Ps30. It falls as the engine degrades.

Sensor 15 (BPR) is the bypass ratio. It rises with degradation.

Sensor 20 and 21 (W31, W32) are the coolant bleed flows of the HPT and LPT. They fall with degradation.

# Failure mode in FD001

FD001 has one operating condition and one fault mode, degradation of the high pressure compressor (HPC). Every engine starts with a normal amount of initial wear and then degrades until it fails. The last recorded cycle of each training engine is the failure point.

# Reading the signals

A gradual, consistent rise across several temperature sensors together points to real engine wear. A jump in a single sensor while all the others stay normal points to a faulty sensor, not an engine fault. Wear that is much faster than other engines of the same age deserves an inspection earlier than the schedule. A small remaining-life estimate with a wide range means the estimate is uncertain and should be confirmed by inspection before any shutdown.