

from math import log
import scipy.constants as spc
from zeroD_model_degradations import DegradationMechanism
from zeroD_model_crossover import Crossover


# Faraday constant (C/mol)
F = spc.value('Faraday constant')

# Molar gas constant (J/K/mol)
R = spc.R

# make these parameters at some point?
TEMPERATURE = 298  # Kelvins, for S.T.P.
NERNST_CONST = (R * TEMPERATURE) / F  # should have n_electrons input option


class ZeroDModel:
    """
    Zero dimensional model for redox flow battery (RFB) cycling [1].

    Parameters
    ----------
    cls_volume : float
        Volume of capacity-limiting side (CLS) reservoir (L).
    ncls_volume : float
        Volume of non-capacity-limiting side (NCLS) reservoir (L).
    cls_start_c_ox : float
        CLS initial concentration of oxidized species (M).
    cls_start_c_red : float
        CLS initial concentration of reduced species (M).
    ncls_start_c_ox : float
        NCLS initial concentration of oxidized species (M).
    ncls_start_c_red : float
        NCLS initial concentration of reduced species (M).
    init_ocv : float
        Cell voltage (formal potentials E_+ - E_-) (V).
        If voltage > 0 then it's a Full cell.
        If voltage = 0 then it's a Symmetric cell.
    resistance : float
        Cell ohmic resistance (ohms).
    k_0_cls : float
        Electrochemical rate constant, CLS redox couple (cm/s).
    k_0_ncls : float
        Electrochemical rate constant, NCLS redox couple (cm/s).
    alpha_cls : float
        Charge transfer coefficient of CLS redox couple, dimensionless.
        Default is 0.5, which is standard in electrochemistry.
    alpha_ncls : float
        Charge transfer coefficient of NCLS redox couple, dimensionless.
        Default is 0.5, which is standard in electrochemistry.
    geometric_area : float
        Geometric area of cell (cm^2).
        Default is 5.0, a typical lab-scale cell size.
    cls_negolyte : bool
        If True, negolyte is the CLS.
    time_increment : float
        Simulation time step (s).
        Default is 0.01, providing adequate balance of accuracy vs compute time.
    k_mt : float
        Mass transport coefficient (cm/s).
        Default is 0.8, as used in [1].
    roughness_factor : float
        Roughness factor, dimensionless.
        Total surface area divided by geometric surface area.
        Default is 26.0, as used in [1].
    n_cls : int
        Number of electrons transferred per active species molecule in the CLS
    n_ncls : int
        Number of electrons transferred per active species molecule in the NCLS


    Notes
    -----
    All equations are adapted from [1]. If ZeroDModel has been
    significant to your research please cite the paper.

    [1] Modak, S.; Kwabi, D. G. A Zero-Dimensional Model for Electrochemical
    Behavior and Capacity Retention in Organic Flow Cells, Journal of The
    Electrochemical Society, 168, 2021, 080528.
    """

    def __init__(self, cls_volume: float, ncls_volume: float, cls_start_c_ox: float, cls_start_c_red: float,
                 ncls_start_c_ox: float, ncls_start_c_red: float, init_ocv: float, resistance: float, k_0_cls: float,
                 k_0_ncls: float, alpha_cls: float = 0.5, alpha_ncls: float = 0.5, geometric_area: float = 5.0,
                 cls_negolyte: bool = True, time_increment: float = 0.01, k_mt: float = 0.8,
                 roughness_factor: float = 26.0, n_cls: int = 1, n_ncls: int = 1) -> None:
        """Initialize ZeroDModel"""
        self.cls_volume = cls_volume
        self.ncls_volume = ncls_volume
        self.c_ox_cls = cls_start_c_ox
        self.c_red_cls = cls_start_c_red
        self.c_ox_ncls = ncls_start_c_ox
        self.c_red_ncls = ncls_start_c_red
        self.init_ocv = init_ocv
        self.resistance = resistance
        self.k_0_cls = k_0_cls
        self.k_0_ncls = k_0_ncls
        self.alpha_cls = alpha_cls
        self.alpha_ncls = alpha_ncls
        self.geometric_area = geometric_area
        self.cls_negolyte = cls_negolyte
        self.time_increment = time_increment
        self.k_mt = k_mt
        self.const_i_ex = F * roughness_factor * self.geometric_area
        self.n_cls = n_cls
        self.n_ncls = n_ncls

        if any(x <= 0.0 for x in [self.cls_volume, self.ncls_volume, self.k_0_cls, self.k_0_ncls, self.geometric_area,
                                  self.time_increment, self.k_mt, self.const_i_ex]):
            raise ValueError("A variable has been set negative/zero")

        if any(x < 0.0 for x in [self.init_ocv, self.resistance]):
            raise ValueError("'init_ocv' and 'resistance' must be zero or positive")

        if not 0.0 < self.alpha_cls < 1.0 or not 0.0 < self.alpha_ncls < 1.0:
            raise ValueError("Alpha parameters must be between 0.0 and 1.0")

    def _exchange_current(self) -> tuple[float, float]:
        """
        Calculates exchange current (i_0) of redox couples in the CLS and NCLS.
        Value returned is in Amps.

        Returns
        -------
        i_0_cls : float
            Exchange current of CLS redox couple
            at a given timestep (A).
        i_0_ncls : float
            Exchange current of NCLS redox couple
            at a given timestep (A).

        """
        # division by 1000 for conversion from L to cm^3
        i_0_cls = (self.const_i_ex * self.k_0_cls * (self.c_red_cls ** self.alpha_cls)
                   * (self.c_ox_cls ** (1 - self.alpha_cls)) * 0.001)
        i_0_ncls = (self.const_i_ex * self.k_0_ncls * (self.c_red_ncls ** self.alpha_ncls)
                    * (self.c_ox_ncls ** (1 - self.alpha_ncls)) * 0.001)
        return i_0_cls, i_0_ncls

    def _limiting_current(self, c_lim: float) -> float:
        """
        Calculates limiting current (i_lim) for a single reservoir.
        Value returned is in Amps.
        This is equation 6 of [1].
        """
        # div by 1000 for conversion from L to cm^3
        # will require n electrons param
        return F * self.k_mt * c_lim * self.geometric_area * 0.001

    def limiting_concentration(self, charge: bool) -> tuple[float, float]:
        """
        Selects limiting concentration and calculates limiting current for CLS and NCLS.

        Parameters
        ----------
        charge : bool
            Positive if charging, negative if discharging.

        Returns
        -------
        i_lim_cls : float
            Limiting current of CLS redox couple
            at a given timestep (A).
        i_lim_ncls : float
            Limiting current of NCLS redox couple
            at a given timestep (A).

        """
        if (self.cls_negolyte and charge) or (not self.cls_negolyte and not charge):
            i_lim_cls = self._limiting_current(self.c_ox_cls)
            i_lim_ncls = self._limiting_current(self.c_red_ncls)
        else:
            i_lim_cls = self._limiting_current(self.c_red_cls)
            i_lim_ncls = self._limiting_current(self.c_ox_ncls)

        return i_lim_cls, i_lim_ncls

    @staticmethod
    def _activation_overpotential(current: float, i_0_cls: float, i_0_ncls: float) -> float:
        """
        Calculates overall cell activation overpotential.
        This is equation 4 of [1].

        Parameters
        ----------
        current : float
            Instantaneous current flowing (A).
        i_0_cls : float
            Exchange current of CLS redox couple
            at a given timestep (A).
        i_0_ncls : float
            Exchange current of NCLS redox couple
            at a given timestep (A).

        Returns
        -------
        n_act : float
            Combined (CLS+NCLS) activation overpotential (V).
        """

        z_cls = abs(current) / (2 * i_0_cls)
        z_ncls = abs(current) / (2 * i_0_ncls)
        n_act = NERNST_CONST * (log(z_ncls + ((z_ncls**2) + 1)**0.5) + log(z_cls + ((z_cls**2) + 1)**0.5))
        return n_act

    def negative_concentrations(self) -> bool:
        """Return True if any concentration is negative"""
        return any(x < 0.0 for x in [self.c_ox_cls, self.c_red_cls, self.c_ox_ncls, self.c_red_ncls])

    def _mass_transport_overpotential(self, charge: bool, current: float, i_lim_cls: float, i_lim_ncls: float) -> float:
        """
        Calculates overall cell mass transport overpotential.
        This is equation 8 of [1].

        Parameters
        ----------
        charge : bool
            Positive if charging, negative if discharging.
        current : float
            Instantaneous current flowing (A).
         i_lim_cls : float
            Limiting current of CLS redox couple
            at a given timestep (A).
        i_lim_ncls : float
            Limiting current of NCLS redox couple
            at a given timestep (A).

        Returns
        -------
        n_mt : float
            Combined (CLS+NCLS) mass transport overpotential (V).

        """
        # Raise ValueError if a negative concentration is detected
        if self.negative_concentrations():
            raise ValueError('Negative concentration detected')

        c_tot_cls = self.c_red_cls + self.c_ox_cls
        c_tot_ncls = self.c_red_ncls + self.c_ox_ncls

        i = abs(current)

        if (self.cls_negolyte and charge) or (not self.cls_negolyte and not charge):
            n_mt = NERNST_CONST * log((1 - ((c_tot_cls * i) / ((self.c_red_cls * i_lim_cls) + (self.c_ox_cls * i))))
                                      * (1 - ((c_tot_ncls * i) / ((self.c_ox_ncls * i_lim_ncls)
                                                                  + (self.c_red_ncls * i)))))
        else:
            n_mt = NERNST_CONST * log(((1 - ((c_tot_cls * i) / ((self.c_ox_cls * i_lim_cls) + (self.c_red_cls * i))))
                                       * (1 - ((c_tot_ncls * i) / ((self.c_red_ncls * i_lim_ncls)
                                                                   + (self.c_ox_ncls * i))))))
        return n_mt

    def total_overpotential(self, current: float, charge: bool,
                            i_lim_cls: float, i_lim_ncls: float) -> tuple[float, float, float]:
        """
        Calculates total cell overpotential.
        This is the overpotentials of equation 2 in [1].

        Parameters
        ----------
        current : float
            Instantaneous current flowing (A).
        charge : bool
            Positive if charging, negative if discharging.
        i_lim_cls : float
            Limiting current of CLS redox couple
            at a given timestep (A).
        i_lim_ncls : float
            Limiting current of NCLS redox couple
            at a given timestep (A).

        Returns
        -------
        n_loss : float
            Total cell overpotential (V).
        n_act : float
            Total activation overpotential (V).
        n_mt : float
            Total mass transport overpotential (V).

        """

        i_0_cls, i_0_ncls = self._exchange_current()
        # calculate overpotentials
        n_ohmic = abs(current)*self.resistance
        n_act = self._activation_overpotential(current, i_0_cls, i_0_ncls)
        n_mt = self._mass_transport_overpotential(charge, current, i_lim_cls, i_lim_ncls)

        n_loss = n_ohmic + n_act + n_mt

        return n_loss, n_act, n_mt

    def open_circuit_voltage(self) -> float:
        """
        Nernstian calculation of cell's open circuit voltage.
        This is equivalent to equation 3 of [1].

        Returns
        -------
        ocv : float
            Cell open circuit voltage (V).

        """

        # Raise ValueError if a negative concentration is detected
        if self.negative_concentrations():
            raise ValueError('Negative concentration detected')

        # will need n_electrons input
        # CLS is negolyte
        if self.cls_negolyte:
            ocv = (self.init_ocv
                   + (NERNST_CONST * log(self.c_red_cls / self.c_ox_cls))
                   + (NERNST_CONST * log(self.c_ox_ncls / self.c_red_ncls)))

        # CLS is posolyte
        else:
            ocv = (self.init_ocv
                   - (NERNST_CONST * log(self.c_red_cls / self.c_ox_cls))
                   - (NERNST_CONST * log(self.c_ox_ncls / self.c_red_ncls)))
        return ocv

    @staticmethod
    def cell_voltage(ocv: float, losses: float, charge: bool) -> float:
        """If charging, add overpotentials to OCV, else subtract them"""
        return ocv + losses if charge else ocv - losses

    def coulomb_counter(self, current: float,
                        cls_degradation: DegradationMechanism = None,
                        ncls_degradation: DegradationMechanism = None,
                        crossover_params: Crossover = None) -> tuple[float, float]:
        """
        Updates all species' concentrations at each timestep.
        Contributions from faradaic current, (optional) degradations
        mechanisms_list, and (optional) crossover mechanisms_list.

        Parameters
        ----------
        current : float
            Instantaneous current flowing (A).
        cls_degradation : DegradationMechanism, optional
            Degradation class instance for CLS.
        ncls_degradation: DegradationMechanism, optional
            Degradation class instance for NCLS.
        crossover_params : Crossover, optional
            Crossover class instance

        Returns
        -------
        delta_ox : float
            Concentration difference (CLS-NCLS) of oxidized species (M).
        delta_red : float
            Concentration difference (CLS-NCLS) of reduced species (M).

        """

        # Coulomb counting based solely on current
        direction = 1 if self.cls_negolyte else -1
        delta_cls = ((self.time_increment * current) / (F * self.cls_volume)) * direction
        delta_ncls = ((self.time_increment * current) / (F * self.ncls_volume)) * direction

        # update CLS and NCLS concentrations
        c_ox_cls = self.c_ox_cls - delta_cls
        c_red_cls = self.c_red_cls + delta_cls
        c_ox_ncls = self.c_ox_ncls + delta_ncls
        c_red_ncls = self.c_red_ncls - delta_ncls

        # for no crossover situation
        delta_ox = 0.0
        delta_red = 0.0

        # Coulomb counting from optional degradation/crossover mechanisms_list
        if cls_degradation is not None:
            c_ox_cls, c_red_cls = cls_degradation.degrade(c_ox_cls, c_red_cls, self.time_increment)

        if ncls_degradation is not None:
            c_ox_ncls, c_red_ncls = ncls_degradation.degrade(c_ox_ncls, c_red_ncls, self.time_increment)

        if crossover_params is not None:
            (c_ox_cls, c_red_cls, c_ox_ncls, c_red_ncls, delta_ox,
             delta_red) = crossover_params.crossover(c_ox_cls, c_red_cls, c_ox_ncls, c_red_ncls, self.cls_volume,
                                                     self.ncls_volume, self.time_increment)
        # update concentrations to self
        self.c_ox_cls = c_ox_cls
        self.c_red_cls = c_red_cls
        self.c_ox_ncls = c_ox_ncls
        self.c_red_ncls = c_red_ncls

        return delta_ox, delta_red

    @staticmethod
    def state_of_charge(c_ox_cls, c_red_cls, c_ox_ncls, c_red_ncls) -> tuple[float, float]:
        """Calculate state-of-charge in each reservoir"""
        soc_cls = (c_red_cls / (c_ox_cls + c_red_cls)) * 100
        soc_ncls = (c_red_ncls / (c_ox_ncls + c_red_ncls)) * 100
        return soc_cls, soc_ncls
