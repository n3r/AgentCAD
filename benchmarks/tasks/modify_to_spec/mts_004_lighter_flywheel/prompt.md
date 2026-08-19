The project already holds the part `flywheel`. The engine has a new rotating
mass budget and the wheel has to come down to meet it.

Requirement: the flywheel must weigh **4.2 kg (4200 g) or less**. It weighs
about 4684 g today.

Constraints:

- Keep the Ø200 mm outside diameter and the six-bolt Ø56 mm crank bolt circle
  exactly as they are: `diameter`, `bolt_circle_d` and `n_bolts` may not move.
- `thickness` is the only parameter you may change, it must land on a **whole
  millimetre**, and it must stay as **thick** as the mass budget allows —
  inertia is the point of a flywheel, so a wheel thinner than the budget
  requires is a rejected design.

Datum: unchanged. The friction (clutch) face lies on Z = 0 and the disc extends
into +Z; the pilot-bore axis is the Z axis and the wheel is centred on the
origin.
