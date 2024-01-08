"""
Classes to define electrochemical cycling protocols.
"""

from abc import ABC, abstractmethod
from enum import Enum

from scipy.optimize import fsolve

from .crossover import Crossover
from .degradation import DegradationMechanism
from .redox_flow_cell import ZeroDModel


class CyclingProtocolResults:
    """
    A container of the simulation result data.

    Parameters
    ----------
    duration : float
        Simulation time (s).
    time_increment: float
        Simulation time step (s).
    charge_first: bool
        True if CLS charges first, False if CLS discharges first.

    """

    def __init__(self, duration: float, time_increment: float, charge_first: bool = True):
        self.duration = duration
        self.time_increment = time_increment
        self.size = int(duration / time_increment)
        self.charge_first = charge_first
        self.compute_soc = True

        self.step = 0
        self.step_time = [0.0] * self.size
        self.step_is_charge = [False] * self.size

        self.current = [0.0] * self.size
        self.cell_v = [0.0] * self.size
        self.ocv = [0.0] * self.size

        self.c_ox_cls = [0.0] * self.size
        self.c_red_cls = [0.0] * self.size
        self.c_ox_ncls = [0.0] * self.size
        self.c_red_ncls = [0.0] * self.size
        self.delta_ox = [0.0] * self.size
        self.delta_red = [0.0] * self.size
        self.soc_cls = [0.0] * self.size
        self.soc_ncls = [0.0] * self.size

        self.act = [0.0] * self.size
        self.mt = [0.0] * self.size
        self.loss = [0.0] * self.size

        # Total number of cycles is unknown at start, thus sizes are undetermined
        self.half_cycles = 0
        self.capacity = 0.0
        self.half_cycle_capacity = []
        self.half_cycle_time = []
        self.half_cycle_is_charge = []
        self.charge_cycle_capacity = []
        self.charge_cycle_time = []
        self.discharge_cycle_capacity = []
        self.discharge_cycle_time = []

    def record_step(self, cell_model: ZeroDModel, charge: bool, current: float, cell_v: float, ocv: float,
                    n_act: float = 0.0, n_mt: float = 0.0, losses: float = 0.0):
        # Update capacity
        self.capacity += abs(current) * cell_model.time_increment

        # Record current, voltages, and charge
        self.current[self.step] = current
        self.cell_v[self.step] = cell_v
        self.ocv[self.step] = ocv
        self.step_is_charge[self.step] = charge

        # Record overpotentials and total loss
        self.act[self.step] = n_act
        self.mt[self.step] = n_mt
        self.loss[self.step] = losses

        # Record species concentrations
        cls_ox = cell_model.c_ox_cls
        cls_red = cell_model.c_red_cls
        ncls_ox = cell_model.c_ox_ncls
        ncls_red = cell_model.c_red_ncls
        self.c_ox_cls[self.step] = cls_ox
        self.c_red_cls[self.step] = cls_red
        self.c_ox_ncls[self.step] = ncls_ox
        self.c_red_ncls[self.step] = ncls_red
        self.delta_ox[self.step] = cell_model.delta_ox
        self.delta_red[self.step] = cell_model.delta_red

        # Compute state-of-charge
        if self.compute_soc:
            if cls_ox + cls_red == 0.0 or ncls_ox + ncls_red == 0.0:
                self.compute_soc = False
            else:
                self.soc_cls[self.step] = (cls_red / (cls_ox + cls_red)) * 100
                self.soc_ncls[self.step] = (ncls_red / (ncls_ox + ncls_red)) * 100

        # Record time and increment the step
        self.step_time[self.step] = self.time_increment * (self.step + 1)
        self.step += 1

    def record_half_cycle(self, charge: bool):
        time = self.step * self.time_increment
        self.half_cycle_capacity.append(self.capacity)
        self.half_cycle_time.append(time)
        self.half_cycle_is_charge.append(charge)
        self.half_cycles += 1

        if charge:
            self.charge_cycle_capacity.append(self.capacity)
            self.charge_cycle_time.append(time)
        else:
            self.discharge_cycle_capacity.append(self.capacity)
            self.discharge_cycle_time.append(time)

        self.capacity = 0.0


class CycleStatus(str, Enum):
    NORMAL = 'normal'
    NEGATIVE_CONCENTRATIONS = 'negative species concentrations'
    VOLTAGE_LIMIT_REACHED = 'voltage limits reached'
    CURRENT_CUTOFF_REACHED = 'current cutoffs reached'
    LIMITING_CURRENT_REACHED = 'current has exceeded the limiting currents for the cell concentrations'
    LOW_CAPACITY = 'capacity is less than 1 coulomb'
    TIME_DURATION_REACHED = 'time duration reached'


class CycleMode(ABC):
    def __init__(
            self,
            charge: bool,
            cell_model: ZeroDModel,
            results: CyclingProtocolResults,
            update_concentrations: callable,
            current_lim_cls: float = None,
            current_lim_ncls: float = None
    ):
        self.charge = charge
        self.cell_model = cell_model
        self.results = results
        self.update_concentrations = update_concentrations

        if not current_lim_cls or not current_lim_ncls:
            current_lim_cls, current_lim_ncls = self.cell_model.limiting_concentration(self.charge)
        self.current_lim_cls = current_lim_cls
        self.current_lim_ncls = current_lim_ncls

    @abstractmethod
    def validate(self) -> CycleStatus:
        raise NotImplementedError

    @abstractmethod
    def cycle_step(self) -> CycleStatus:
        raise NotImplementedError

    def check_capacity(self, cycle_status: CycleStatus) -> CycleStatus:
        # End the simulation if the half cycle capacity nears zero
        if self.results.capacity < 1.0 and self.results.half_cycles > 2:
            return CycleStatus.LOW_CAPACITY

        return cycle_status

    def check_time(self, cycle_status: CycleStatus):
        if cycle_status != CycleStatus.NORMAL:
            return cycle_status

        # End the simulation if the time limit is reached
        if self.results.step >= self.results.size:
            return CycleStatus.TIME_DURATION_REACHED

        return CycleStatus.NORMAL


class ConstantCurrentCycleMode(CycleMode):
    def __init__(
            self,
            charge: bool,
            cell_model: ZeroDModel,
            results: CyclingProtocolResults,
            update_concentrations: callable,
            current: float,
            voltage_limit: float,
            voltage_limit_capacity_check: bool = True
    ):
        super().__init__(charge, cell_model, results, update_concentrations)
        self.current = current
        self.voltage_limit = voltage_limit
        self.voltage_limit_capacity_check = voltage_limit_capacity_check

    def validate(self) -> CycleStatus:
        if abs(self.current) >= min(self.current_lim_cls, self.current_lim_ncls):
            return CycleStatus.LIMITING_CURRENT_REACHED

        losses, *_ = self.cell_model.total_overpotential(self.current, self.current_lim_cls, self.current_lim_ncls)
        ocv = self.cell_model.open_circuit_voltage()
        cell_v = self.cell_model.cell_voltage(ocv, losses, self.charge)

        if abs(cell_v) >= abs(self.voltage_limit):
            return CycleStatus.VOLTAGE_LIMIT_REACHED

        return CycleStatus.NORMAL

    def cycle_step(self) -> CycleStatus:
        cycle_status = CycleStatus.NORMAL

        # Calculate species' concentrations
        self.update_concentrations(self.current)

        # Handle edge case where the voltage limits are never reached
        if self.cell_model.negative_concentrations():
            self.cell_model.revert_concentrations()
            return self.check_capacity(CycleStatus.NEGATIVE_CONCENTRATIONS)

        # Calculate overpotentials and the resulting cell voltage
        losses, n_act, n_mt = self.cell_model.total_overpotential(self.current, self.current_lim_cls, self.current_lim_ncls)
        ocv = self.cell_model.open_circuit_voltage()
        cell_v = self.cell_model.cell_voltage(ocv, losses, self.charge)

        # Check if the voltage limit is reached
        if abs(cell_v) >= abs(self.voltage_limit):
            cycle_status = CycleStatus.VOLTAGE_LIMIT_REACHED
            if self.voltage_limit_capacity_check:
                cycle_status = self.check_capacity(cycle_status)

        # Update results
        self.results.record_step(self.cell_model, self.charge, self.current, cell_v, ocv, n_act, n_mt, losses)

        return self.check_time(cycle_status)


class ConstantVoltageCycleMode(CycleMode):
    def __init__(
            self,
            charge: bool,
            cell_model: ZeroDModel,
            results: CyclingProtocolResults,
            update_concentrations: callable,
            current_cutoff: float,
            voltage_limit: float,
            current_estimate: float,
            current_lim_cls: float = None,
            current_lim_ncls: float = None
    ):
        super().__init__(charge, cell_model, results, update_concentrations, current_lim_cls, current_lim_ncls)
        self.update_concentrations = update_concentrations
        self.current_cutoff = current_cutoff
        self.voltage_limit = voltage_limit

        self.current = current_estimate  # this can be too big but solver can handle

    def validate(self) -> CycleStatus:
        return CycleStatus.NORMAL

    def cycle_step(self) -> CycleStatus:
        if not self.current:
            # Set initial current guess as a function of the limiting currents, however, we want to ensure that the
            # guess is less than the limiting currents to avoid log errors in the overpotential calculations
            self.current = 0.99 * min(self.current_lim_cls, self.current_lim_ncls)
        elif abs(self.current) >= min(self.current_lim_cls, self.current_lim_ncls):
            return CycleStatus.LIMITING_CURRENT_REACHED

        ocv = self.cell_model.open_circuit_voltage()

        # Adapting the solver's guess to the updated current
        self._find_min_current(ocv)

        # Check if the current is below cutoffs
        if abs(self.current) <= abs(self.current_cutoff):
            self.cell_model.revert_concentrations()
            return self.check_capacity(CycleStatus.CURRENT_CUTOFF_REACHED)

        self.update_concentrations(self.current)

        # Check if any reactant remains
        if self.cell_model.negative_concentrations():
            self.cell_model.revert_concentrations()
            return self.check_capacity(CycleStatus.NEGATIVE_CONCENTRATIONS)

        # Update results
        self.results.record_step(self.cell_model, self.charge, self.current, self.voltage_limit, ocv)

        return self.check_time(CycleStatus.NORMAL)

    def _find_min_current(self, ocv: float):
        """
        Method wrapper to solve for current during constant voltage cycling.
        Attempts to minimize the difference of voltage, OCV, and losses (function of current).

        Returns
        -------
        min_current : float
            Solved current at given timestep of constant voltage cycling (A).
        """
        def solver(current: float) -> float:
            """Numerical solver for current during constant voltage cycling"""
            loss_solve, *_ = self.cell_model.total_overpotential(current, self.current_lim_cls, self.current_lim_ncls)
            current_direction = 1 if self.charge else -1
            return self.voltage_limit - ocv - current_direction * loss_solve

        min_current, *_ = fsolve(solver, self.current, xtol=1e-5)
        self.current = min_current


class CyclingProtocol(ABC):
    """
    Base class to be overridden by specific cycling protocol choice.

    Parameters
    ----------
    voltage_limit_charge : float
        Voltage above which cell will switch to discharge (V).
    voltage_limit_discharge : float
        Voltage below which cell will switch to charge (V).
    charge_first : bool
        True if CLS charges first, False if CLS discharges first.

    """

    def __init__(self, voltage_limit_charge: float, voltage_limit_discharge: float, charge_first: bool = True):
        self.voltage_limit_charge = voltage_limit_charge
        self.voltage_limit_discharge = voltage_limit_discharge
        self.charge_first = charge_first

    @abstractmethod
    def run(
            self,
            duration: int,
            cell_model: ZeroDModel,
            degradation: DegradationMechanism = None,
            cls_degradation: DegradationMechanism = None,
            ncls_degradation: DegradationMechanism = None,
            crossover: Crossover = None,
    ) -> CyclingProtocolResults:
        """Applies a cycling protocol and (optional) degradation mechanisms to a cell model"""
        raise NotImplementedError

    @staticmethod
    def _validate_cycle_values(value: float, value_charge: float, value_discharge: float, name: str) -> tuple[float, float]:
        if value is not None and (value_charge is not None or value_discharge is not None):
            raise ValueError(f"Cannot specify both '{name}' and f'{name}_(dis)charge'")

        if value is not None:
            if value <= 0.0:
                raise ValueError(f"'{name}' must be > 0.0")
            value_charge = value
            value_discharge = -value

        if value_charge <= 0.0:
            raise ValueError(f"'{name}_charge' must be > 0.0")
        if value_discharge >= 0.0:
            raise ValueError(f"'{name}_discharge' must be < 0.0")

        return value_charge, value_discharge

    def _validate_protocol(
            self,
            duration: int,
            cell_model: ZeroDModel,
            degradation: DegradationMechanism,
            cls_degradation: DegradationMechanism,
            ncls_degradation: DegradationMechanism,
            crossover: Crossover
    ) -> tuple[CyclingProtocolResults, callable]:
        if not self.voltage_limit_discharge < cell_model.init_ocv < self.voltage_limit_charge:
            raise ValueError("Ensure that 'voltage_limit_discharge' < 'init_ocv' < 'voltage_limit_charge'")
        if cell_model.init_ocv > 0.0 and self.voltage_limit_discharge < 0.0:
            raise ValueError("Ensure that 'voltage_limit_discharge' >= 0.0 when 'init_ocv' > 0.0")

        if degradation is not None and (cls_degradation is not None or ncls_degradation is not None):
            raise ValueError("Cannot specify both 'degradation' and '(n)cls_degradation'")

        if degradation is not None:
            cls_degradation = degradation
            ncls_degradation = degradation

        if cell_model.negative_concentrations():
            raise ValueError('Negative concentration detected')

        def update_concentrations(i: float):
            return cell_model.coulomb_counter(i, cls_degradation, ncls_degradation, crossover)

        # Initialize data results object to be sent to user
        results = CyclingProtocolResults(duration, cell_model.time_increment, self.charge_first)

        return results, update_concentrations


class ConstantCurrent(CyclingProtocol):
    """
    Provides a constant current (CC) cycling method.

    Parameters
    ----------
    voltage_limit_charge : float
        Voltage above which cell will switch to discharge (V).
    voltage_limit_discharge : float
        Voltage below which cell will switch to charge (V).
    current : float
        Instantaneous current flowing (A).
    charge_first : bool
        True if CLS charges first, False if CLS discharges first.

    """

    def __init__(
            self,
            voltage_limit_charge: float,
            voltage_limit_discharge: float,
            current: float = None,
            current_charge: float = None,
            current_discharge: float = None,
            charge_first: bool = True,
    ):
        super().__init__(voltage_limit_charge, voltage_limit_discharge, charge_first)
        self.current_charge, self.current_discharge = self._validate_cycle_values(
            current, current_charge, current_discharge, 'current'
        )

    def run(
            self,
            duration: int,
            cell_model: ZeroDModel,
            degradation: DegradationMechanism = None,
            cls_degradation: DegradationMechanism = None,
            ncls_degradation: DegradationMechanism = None,
            crossover: Crossover = None
    ) -> CyclingProtocolResults:
        """

        Parameters
        ----------
        duration : int
            Simulation time (s).
        cell_model : ZeroDModel
            Defined cell parameters for simulating.
        degradation : DegradationMechanism, optional
            Degradation mechanism(s) applied to CLS and NCLS.
        cls_degradation : DegradationMechanism, optional
            Degradation mechanism(s) applied to CLS.
        ncls_degradation : DegradationMechanism, optional
            Degradation mechanism(s) applied to NCLS.
        crossover : Crossover, optional
            Crossover mechanism applied to cell.

        Returns
        -------
        results : CyclingProtocolResults
            Object with results from simulation

        """

        results, update_concentrations = self._validate_protocol(
            duration, cell_model, degradation, cls_degradation, ncls_degradation, crossover
        )

        def get_cycle_mode(charge):
            return ConstantCurrentCycleMode(
                charge, cell_model, results, update_concentrations,
                self.current_charge if charge else self.current_discharge,
                self.voltage_limit_charge if charge else self.voltage_limit_discharge
            )

        cycle_mode = get_cycle_mode(self.charge_first)
        cycle_status = cycle_mode.validate()
        if cycle_status != CycleStatus.NORMAL:
            cycle_mode = get_cycle_mode(not self.charge_first)
            new_cycle_status = cycle_mode.validate()
            if new_cycle_status != CycleStatus.NORMAL:
                raise ValueError(cycle_status)
            cycle_name = 'charge' if self.charge_first else 'discharge'
            print(f'Skipping to {cycle_name} cycle: {cycle_status}')

        print(f'{duration} sec of cycling, time steps: {cell_model.time_increment} sec')
        cycle_status = CycleStatus.NORMAL

        while cycle_status == CycleStatus.NORMAL:
            cycle_status = cycle_mode.cycle_step()

            if cycle_status in [CycleStatus.NEGATIVE_CONCENTRATIONS, CycleStatus.VOLTAGE_LIMIT_REACHED]:
                # Record info for the half cycle
                results.record_half_cycle(cycle_mode.charge)

                # Start the next half cycle
                cycle_mode = get_cycle_mode(not cycle_mode.charge)
                cycle_status = CycleStatus.NORMAL

        print(f'Simulation stopped after {results.step} time steps: {cycle_status}.')
        return results


class ConstantVoltage(CyclingProtocol):
    """
    Provides a constant voltage (CV) cycling method.

    Parameters
    ----------
    current_cutoff_charge : float
        Current below which cell will switch to discharge (V).
    current_cutoff_discharge : float
        Current above which cell will switch to charge (V).
    charge_first : bool
        True if CLS charges first, False if CLS discharges first.

    """

    def __init__(
            self,
            voltage_limit_charge: float,
            voltage_limit_discharge: float,
            current_cutoff: float = None,
            current_cutoff_charge: float = None,
            current_cutoff_discharge: float = None,
            charge_first: bool = True,
    ):
        super().__init__(voltage_limit_charge, voltage_limit_discharge, charge_first)
        self.current_cutoff_charge, self.current_cutoff_discharge = self._validate_cycle_values(
            current_cutoff, current_cutoff_charge, current_cutoff_discharge, 'current_cutoff'
        )

    def run(
            self,
            duration: int,
            cell_model: ZeroDModel,
            degradation: DegradationMechanism = None,
            cls_degradation: DegradationMechanism = None,
            ncls_degradation: DegradationMechanism = None,
            crossover: Crossover = None
    ) -> CyclingProtocolResults:
        """

        Parameters
        ----------
        duration : int
            Simulation time (s).
        cell_model : ZeroDModel
            Defined cell parameters for simulating.
        degradation : DegradationMechanism, optional
            Degradation mechanism(s) applied to CLS and NCLS.
        cls_degradation : DegradationMechanism, optional
            Degradation mechanism(s) applied to CLS.
        ncls_degradation : DegradationMechanism, optional
            Degradation mechanism(s) applied to NCLS.
        crossover : Crossover, optional
            Crossover mechanism applied to cell.

        Returns
        -------
        results : CyclingProtocolResults
            Object with results from simulation

        """

        results, update_concentrations = self._validate_protocol(
            duration, cell_model, degradation, cls_degradation, ncls_degradation, crossover
        )

        def get_cycle_mode(charge, current):
            return ConstantVoltageCycleMode(
                charge, cell_model, results, update_concentrations,
                self.current_cutoff_charge if charge else self.current_cutoff_discharge,
                self.voltage_limit_charge if charge else self.voltage_limit_discharge,
                current
            )

        cycle_mode = get_cycle_mode(self.charge_first, 0.0)
        cycle_status = cycle_mode.validate()
        if cycle_status != CycleStatus.NORMAL:
            raise ValueError(cycle_status)

        print(f'{duration} sec of cycling, time steps: {cell_model.time_increment} sec')

        while cycle_status == CycleStatus.NORMAL:
            cycle_status = cycle_mode.cycle_step()

            if cycle_status in [CycleStatus.CURRENT_CUTOFF_REACHED, CycleStatus.NEGATIVE_CONCENTRATIONS]:
                # Record info for the half cycle
                results.record_half_cycle(cycle_mode.charge)

                # Start the next half cycle
                cycle_mode = get_cycle_mode(not cycle_mode.charge, cycle_mode.current)
                cycle_status = CycleStatus.NORMAL

        print(f'Simulation stopped after {results.step} time steps: {cycle_status}.')
        return results


class ConstantCurrentConstantVoltage(CyclingProtocol):
    """
    Provides a constant current constant voltage (CCCV) cycling method which, in the limit of a high current demanded of
    a cell that it cannot maintain, becomes a constant voltage (CV) cycling method.

    Parameters
    ----------
    voltage_limit_charge : float
        Voltage above which cell will switch to CV mode, charging (V).
    voltage_limit_discharge : float
        Voltage below which cell will switch to CV mode, discharging (V).
    current_cutoff_charge : float
        Current below which CV charging will switch to CC portion of CCCV discharge (A).
    current_cutoff_discharge : float
        Current above which CV discharging will switch to CC portion of CCCV charge (A).
    current : float
        Instantaneous current flowing (A).
    charge_first : bool
        True if CLS charges first, False if CLS discharges first.

    """

    def __init__(
            self,
            voltage_limit_charge: float,
            voltage_limit_discharge: float,
            current_cutoff: float = None,
            current_cutoff_charge: float = None,
            current_cutoff_discharge: float = None,
            current: float = None,
            current_charge: float = None,
            current_discharge: float = None,
            charge_first: bool = True,
    ):
        super().__init__(voltage_limit_charge, voltage_limit_discharge, charge_first)
        self.current_cutoff_charge, self.current_cutoff_discharge = self._validate_cycle_values(
            current_cutoff, current_cutoff_charge, current_cutoff_discharge, 'current_cutoff'
        )
        self.current_charge, self.current_discharge = self._validate_cycle_values(
            current, current_charge, current_discharge, 'current'
        )

    def run(
            self,
            duration: int,
            cell_model: ZeroDModel,
            degradation: DegradationMechanism = None,
            cls_degradation: DegradationMechanism = None,
            ncls_degradation: DegradationMechanism = None,
            crossover: Crossover = None
    ) -> CyclingProtocolResults:
        """

        Parameters
        ----------
        duration : int
            Simulation time (s).
        cell_model : ZeroDModel
            Defined cell parameters for simulating.
        degradation : DegradationMechanism, optional
            Degradation mechanism(s) applied to CLS and NCLS.
        cls_degradation : DegradationMechanism, optional
            Degradation mechanism(s) applied to CLS.
        ncls_degradation : DegradationMechanism, optional
            Degradation mechanism(s) applied to NCLS.
        crossover : Crossover, optional
            Crossover mechanism applied to cell.

        Returns
        -------
        results : CyclingProtocolResults
            Object with results from simulation

        """
        results, update_concentrations = self._validate_protocol(
            duration, cell_model, degradation, cls_degradation, ncls_degradation, crossover
        )

        def get_cc_cycle_mode(charge):
            return ConstantCurrentCycleMode(
                charge, cell_model, results, update_concentrations,
                self.current_charge if charge else self.current_discharge,
                self.voltage_limit_charge if charge else self.voltage_limit_discharge,
                voltage_limit_capacity_check=False
            )

        def get_cv_cycle_mode(charge, current_estimate, current_lim_cls=None, current_lim_ncls=None):
            return ConstantVoltageCycleMode(
                charge, cell_model, results, update_concentrations,
                self.current_cutoff_charge if charge else self.current_cutoff_discharge,
                self.voltage_limit_charge if charge else self.voltage_limit_discharge,
                current_estimate, current_lim_cls, current_lim_ncls
            )

        cycle_mode = get_cc_cycle_mode(self.charge_first)

        # Check if cell needs to go straight to CV
        cycle_status = cycle_mode.validate()
        is_cc_mode = cycle_status == CycleStatus.NORMAL
        if not is_cc_mode:
            print(f'Skipping to CV cycling: {cycle_status}')
            cv_current = self.current_charge if self.charge_first else self.current_discharge
            cycle_mode = get_cv_cycle_mode(self.charge_first, cv_current)
            cycle_status = cycle_mode.validate()

        print(f'{duration} sec of cycling, time steps: {cell_model.time_increment} sec')

        while cycle_status == CycleStatus.NORMAL:
            if is_cc_mode and abs(cycle_mode.current) >= min(cycle_mode.current_lim_cls, cycle_mode.current_lim_ncls):
                print('trouble')  # todo
                is_cc_mode = False

            cycle_status = cycle_mode.cycle_step()

            if cycle_status == CycleStatus.VOLTAGE_LIMIT_REACHED:
                is_cc_mode = False
                cycle_mode = get_cv_cycle_mode(cycle_mode.charge, cycle_mode.current,
                                               cycle_mode.current_lim_cls, cycle_mode.current_lim_ncls)
                cycle_status = CycleStatus.NORMAL

            if cycle_status == CycleStatus.CURRENT_CUTOFF_REACHED:
                # Record info for the half cycle
                results.record_half_cycle(cycle_mode.charge)

                is_cc_mode = True
                cycle_mode = get_cc_cycle_mode(not cycle_mode.charge)
                cycle_status = CycleStatus.NORMAL

            if cycle_status == CycleStatus.NEGATIVE_CONCENTRATIONS:
                # Record info for the half cycle
                results.record_half_cycle(cycle_mode.charge)

                # Start the next half cycle
                if is_cc_mode:
                    cycle_mode = get_cc_cycle_mode(not cycle_mode.charge)
                else:
                    cycle_mode = get_cv_cycle_mode(not cycle_mode.charge, cycle_mode.current)
                cycle_status = CycleStatus.NORMAL

        print(f'Simulation stopped after {results.step} time steps: {cycle_status}.')

        return results

