# PureFocus850 — ASCII Command Reference (condensed)

Extracted from `PF850-Manual-Ver2.4-RW-update-section-930185.pdf` (Section 9, pp.49–57),
cross-checked against the live config files in `C:\Users\Lab\Documents\PF850\`.
Source PDFs: `~/Downloads/PF850-Manual-Ver2.4-...pdf` (full manual, 77 pp),
`~/Downloads/PureFocus850.pdf` (5-pp datasheet only — no commands).
Vendor GUI + firmware: `~/Downloads/PF850 Production Release R11/`.

## Device / connection
- Laser autofocus for infinity-corrected scopes. Head (PF185) sits in the infinity space;
  controller (PF100) connects to host by **USB → virtual COM port**.
- **Serial: 460800 baud, 8 data bits, no parity, 1 stop bit, no flow control.**
- **Every command and response is terminated by `<CR>` (ASCII 0x0D `\r`).**
- Safe to plug/unplug USB with unit powered. Windows USB driver: manual Appendix 1.
- This is a DIFFERENT interface from the XY stage (which uses PriorScientificSDK.dll).
  Use **pyserial** here, not the DLL. (FW `DATE` reports "OptiScan LF", shared lineage,
  but the documented host interface is direct ASCII serial.)

## Protocol conventions
- Query form (no comma) returns a value; set form (`CMD,args`) returns `0` on success.
- Most signal/focus/servo parameters are **per-objective** (see `OBJ`). Load an objective's
  saved params with `OBJ,n` before reading/writing them.
- Errors: `E,<code>` (see Error Codes). Always read the full `<CR>` line; `LIST` returns many
  lines up to and including `END`.

## Operating principle (for context)
- Line sensor (1500 px). Sums A = left of centre, B = right of centre (`PINHOLE c,w`).
  `POS = (A-B)/(A+B)` in [-1,+1]; `ERROR = POS - TARGET`. PID(KP,KI,KD) drives stepper or
  piezo (or DAC in measure mode). C = centre pixel; D = arbitrary region (interface detect).
- Flags: SAMPLE (A+B > SAMPLEL), FOCUS (FOCUSL<C<FOCUSH and |ERROR|<FOCUSR),
  INTERFACE (INTERFACEL<D<INTERFACEH).

## 9.1 Signal settings (per-objective)
| Command | Response | Meaning |
|---|---|---|
| `PINHOLE` | `centre,width` | get pinhole centre/width |
| `PINHOLE,c,w` | `0` | set symmetric A/B regions: width `w` px each side of centre px `c` |
| `LASER` | `LASER,n` | get laser power (0–4095) |
| `LASER,n` | `0` | set laser power (0–4095) |
| `BACKGROUND` | `BACKGROUND,a,b,c,d` | get per-region single-pixel background (0–4095) |
| `BACKGROUND,a,b,c,d` | `…` | set per-region background |

## 9.2 Focus signal (read-mostly)
| Command | Response | Meaning |
|---|---|---|
| `ABCD` | `A,B,C,D,I,S` | region sums (0–6142500), I=focus state, S=sample state |
| `POS` | `f` | position signal (A-B)/(A+B) |
| `OUTPUT` | `f` | current PID output |
| `ERROR` | `f` | error = POS - TARGET |
| `TEST,t` | `0` | test stream: 0 off, 1=TARGET,INPUT,ERROR,OUTPUT, 2=A,B,C,D |

## 9.3 Servo settings (per-objective)
| Command | Response | Meaning |
|---|---|---|
| `SERVO` / `SERVO,b` | `b` / `0` | get/set servo on(1)/off(0). **Enable only when AUTO=2** |
| `KP`/`KI`/`KD` (+ `,n`) | `n` / `0` | get/set PID gains |
| `TARGET` | `0` | set setpoint to current error value |
| `TARGET,f` | `0` | set setpoint, -1≤f≤1 |
| `TARGET,?` | `f` | get target |
| `SERVODIR` / `SERVODIR,n` | `n` / `0` | servo PID sign (-1 or 1), independent of ZD |
| `OUTLIM,min,max` / `OUTLIM` | `0` / `min,max` | PID output limits |

## 9.4 Flag settings (per-objective)
| Command | Response | Meaning |
|---|---|---|
| `SAMPLE` | `b` | sample detected (A+B>SAMPLEL) |
| `SAMPLEL` / `SAMPLEL,n` | `l` / `0` | sample low threshold |
| `FOCUS` | `b` | in-focus state |
| `FOCUSL`/`FOCUSH` (+`,n`) | `n` / `0` | focus C-window low/high |
| `FOCUSR` / `FOCUSR,f` | `f` / `0` | focus error-range threshold |
| `IFP`/`IFP,n`, `TTIF` | … | focus-period / time-to-in-focus diagnostics |
| `INTERFACE`, `INTERFACEH/L` (+`,n`) | … | interface state + D-window thresholds |
| `INHIBIT` / `INHIBIT,i` | `b` / `0` | servo inhibit |
| `FOCUSI` / `FOCUSI,b` | `b` / `0` | servo interrupt |
| `SERVOLIMIT,a,p,m` / `SERVOLIMIT` | `0` / `a,p,m` | stepper soft limits (active, +dist, -dist) |
| `SERVOINLIMIT` | `b` | within active servo range |

## 9.5 Objective parameters
| Command | Response | Meaning |
|---|---|---|
| `OBJ,n` | `0` | load saved params for objective n (1–6) |
| `OBJ` | `n` | get current objective |
| `LIST` | many lines → `END` | dump all current-objective params |

## 9.6 Digipot / offset lens
| Command | Response | Meaning |
|---|---|---|
| `LENS` | `n` | digipot function: 0=focus motor/piezo, 1=offset lens |
| `OF`/`OF,n`, `OFL`/`OFL,n` | … | digipot speed scaling % for focus / offset lens |
| `LENSH` | `0` | home offset lens |
| `LENSV,n` | `0` | move offset lens at velocity n steps/s |
| `LENSP` | `p` | offset lens position in steps (**25600 steps/mm**) |
| `LENS$` | `b` | offset lens motion: 0 idle, 1 moving |
| `LENSACC,a` / `LENSVEL,v` | `0` | offset lens accel / max velocity |
| `LENSG,p` | `0` | move offset lens to step position p |
| `LENSGO,n` | `0` | move offset lens to stored position n (1–5) for current obj |
| `LENSSO,n` / `LENSSO,n,p` | `0` | save current / explicit position p as stored offset n |

## 9.7 Focus drive (Z) — stepper or piezo
| Command | Response | Meaning |
|---|---|---|
| `UPR` / `UPR,n` | `n` / `0` | microns/rev (stepper, default 100) or full travel (piezo) |
| `$` | `n` | focus motion status: 0 idle, 4 moving |
| `PZ` / `PZ,n` | `n` / `0` | focus position in user units (default 100 nm). Stepper only |
| `VZ,f` | `0` | move focus at f µm/s (stepper only) |
| `U` / `D` | `R` | move up / down by C steps |
| `C` / `C,n` | `n` / `0` | default step size for U/D in user units |
| `V,n` | `R` | move focus to position n (user units, default 100 nm) |
| `Z` | `0` | zero current focus position |
| `SMZ`/`SMZ,n`, `SAZ`/`SAZ,n` | … | max focus speed / accel, 1–100% (`,U` variants → µm/s, µm/s²) |
| `SSZ` / `SSZ,s` | `n` / `0` | microsteps per user unit (default 50 → 100 nm at UPR=100) |
| `ZD` / `ZD,d` | `d` / `0` | focus drive direction sign (-1 or 1) |
| `LMT` | `h` | active limit switches (hex): 00 none, 10 +Z, 20 -Z |
| `PIEZO,n` / `PIEZO` | `0` / `p` | raw piezo DAC 0–4095 (=0–10 V), piezo mode |

## 9.8 System
| Command | Response | Meaning |
|---|---|---|
| `DATE` | 2 lines | product / version / build date |
| `SERIAL` | `n` | controller serial number |
| `RESET` | `0` | reset unit, ALL params to default |
| `RESTART` | `0` | restart, saved params kept |
| `FLAG,h` / `FLAG` | `0` / `h` | 32-bit user flag (hex), cleared on power cycle |
| `SAVE` | `0` | persist all params to flash (slow, several s) |
| `CONFIG,m,s` / `CONFIG` | `0` / `m,s` | mode: m=S stepper/P piezo/H measure; s=S slice/L line |
| `KBDLOCK` / `KBDLOCK,b` | `b` / `0` | controller keypad lock |

## 9.9 Advanced
| Command | Response | Meaning |
|---|---|---|
| `@,start,n` | multi-line `@,count,hex…` | raw line-sensor pixels start..start+n-1 (not in AUTO2) |
| `REGION,r` / `REGION,r,s,f` | `s,f` / `0` | get/set region r∈{A,B,C,D} start/finish px (0–1499) |
| `AUTO,n` / `AUTO` | `AUTO,n` / `n` | 0=manual (REFRESH needed), 1=auto, **2=realtime, required for servo (default)** |
| `REFRESH` | `REFRESH,0` | force ABCD update in AUTO,0 |
| `EXPOSURE,n` / `EXPOSURE` | `0` / `n` | line-sensor exposure, microseconds |

## 9.10 Error codes
`E,2` not idle · `E,3` no drive · `E,4` string parse · `E,5` command not found ·
`E,8` value out of range · `E,10..15` argument 1..6 out of range.

## Live config on this machine (`C:\Users\Lab\Documents\PF850\`)
- `SYSTEM.txt`: `CONFIG,S,S` (stepper + slice), `EXPOSURE,65`, `ZD,1`, `SERVODIR,1`,
  `UPR,100`, plus a `PAD,...` line.
- 6 objective files (`OBJ1 5x` … `OBJ6 100x` MPlanFL N) holding per-objective KP/KD/KI,
  LASER, TARGET, FOCUSL/H/R, INTERFACE*, OUTLIM, SAMPLEL/H, PINHOLE, REGION, BACKGROUND,
  LENSSO 1–5, SERVOLIMIT. These mirror the `LIST`/`OBJ` parameter set above.
</content>
</invoke>
