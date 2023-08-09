

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
NERNST_CONST = (R*TEMPERATURE) / F  # should have n_electrons input option


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
    alpha_ncls : float
        Charge transfer coefficient of NCLS redox couple, dimensionless.
    geometric_area : float
        Geometric area of cell (cm^2).
    cls_negolyte : bool
        If True, negolyte is the CLS.
    time_increment : float
        Simulation time step (s).
    k_mt : float
        Mass transport coefficient (cm/s).
    roughness_factor : float
        Roughness factor, dimensionless.
        Surface area divided by geometric surface area.


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
                 roughness_factor: float = 26.0) -> None:
        """Inits ZeroDModel"""
        self.cls_volume = cls_volume
        self.ncls_volume = ncls_volume
        self.cls_start_c_ox = cls_start_c_ox
        self.cls_start_c_red = cls_start_c_red
        self.ncls_start_c_ox = ncls_start_c_ox
        self.ncls_start_c_red = ncls_start_c_red
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

    # option for just measuring capacity over time, doesn't need to make all arrays?
    def starting_concentrations(self):
        return self.cls_start_c_ox, self.cls_start_c_red, self.ncls_start_c_ox, self.ncls_start_c_red

    @staticmethod
    def _current_direction(charge: bool) -> int:
        """Make current positive for charge, negative for discharge"""
        return 1 if charge else -1

    def i_exchange_current(self, c_ox_cls: float, c_red_cls: float, c_ox_ncls: float,
                           c_red_ncls: float) -> tuple[float, float]:
        """
        Calculates exchange current of redox couples in the CLS and NCLS.

        Parameters
        ----------
        c_ox_cls: float
            Concentration of oxidized species in CLS
             at a given timestep (M).
        c_red_cls: float
            Concentration of reduced species in CLS
             at a given timestep (M).
        c_ox_ncls: float
            Concentration of oxidized species in NCLS
             at a given timestep (M).
        c_red_ncls: float
            Concentration of reduced species in NCLS
             at a given timestep (M).

        Returns
        -------
        i_0_cls : float
            Exchange current of CLS redox couple
            at a given timestep (A).
        i_0_ncls : float
            Exchange current of NCLS redox couple
            at a given timestep (A)

        """
        # division by 1000 for conversion from mol/L to mol/cm^3
        i_0_cls = (self.const_i_ex * self.k_0_cls * (c_red_cls ** self.alpha_cls)
                   * (c_ox_cls ** (1 - self.alpha_cls)) * 0.001)
        i_0_ncls = (self.const_i_ex * self.k_0_ncls * (c_red_ncls ** self.alpha_ncls)
                    * (c_ox_ncls ** (1 - self.alpha_ncls)) * 0.001)
        return i_0_cls, i_0_ncls

    def i_limiting(self, c_lim: float) -> float:
        """Calculates limiting current for a single reservoir.
        This is equation 6 of [1].
        """
        # div by 1000 for conversion from mol/L to mol/cm^3
        # will require n electrons param
        return F * self.k_mt * c_lim * self.geometric_area * 0.001

    def limiting_reactant_selector(self, charge: bool, c_ox_cls: float, c_red_cls: float, c_ox_ncls: float,
                                   c_red_ncls: float) -> tuple[float, float]:
        """Selects limiting concentration and calculates limiting current for CLS and NCLS."""
        if (self.cls_negolyte and charge) or (not self.cls_negolyte and not charge):
            i_lim_cls = self.i_limiting(c_ox_cls)
            i_lim_ncls = self.i_limiting(c_red_ncls)
        else:
            i_lim_cls = self.i_limiting(c_red_cls)
            i_lim_ncls = self.i_limiting(c_ox_ncls)

        return i_lim_cls, i_lim_ncls

    @staticmethod
    def n_activation(current: float, i_0_cls: float, i_0_ncls: float) -> float:
        """
        This is equation 4 of [1].
        Parameters
        ----------
        current
        i_0_cls
        i_0_ncls

        Returns
        -------

        """

        z_cls = abs(current) / (2 * i_0_cls)
        z_ncls = abs(current) / (2 * i_0_ncls)
        n_act = NERNST_CONST * (log(z_ncls + ((z_ncls**2) + 1)**0.5) + log(z_cls + ((z_cls**2) + 1)**0.5))
        return n_act

    @staticmethod
    def _negative_concentrations(c_ox_cls: float, c_red_cls: float, c_ox_ncls: float, c_red_ncls: float) -> bool:
        """Return True if any concentration is negative"""
        return any(x < 0.0 for x in [c_ox_cls, c_red_cls, c_ox_ncls, c_red_ncls])

    def n_mass_transport(self, charge: bool, current: float, c_ox_cls: float, c_red_cls: float,
                         c_ox_ncls: float, c_red_ncls: float, i_lim_cls: float, i_lim_ncls: float) -> float:
        """
        This is equation 8 of [1].

        Parameters
        ----------
        charge
        current
        c_ox_cls
        c_red_cls
        c_ox_ncls
        c_red_ncls
        i_lim_cls
        i_lim_ncls

        Returns
        -------

        """
        # Raise ValueError if a negative concentration is detected
        if ZeroDModel._negative_concentrations(c_ox_cls, c_red_cls, c_ox_ncls, c_red_ncls):
            raise ValueError('Negative concentration detected')

        c_tot_cls = c_red_cls + c_ox_cls
        c_tot_ncls = c_red_ncls + c_ox_ncls

        i = abs(current)

        if (self.cls_negolyte and charge) or (not self.cls_negolyte and not charge):
            n_mt = NERNST_CONST * log((1 - ((c_tot_cls * i) / ((c_red_cls * i_lim_cls) + (c_ox_cls * i))))
                                      * (1 - ((c_tot_ncls * i) / ((c_ox_ncls * i_lim_ncls) + (c_red_ncls * i)))))
        else:
            n_mt = NERNST_CONST * log(((1 - ((c_tot_cls * i) / ((c_ox_cls * i_lim_cls) + (c_red_cls * i))))
                                       * (1 - ((c_tot_ncls * i) / ((c_red_ncls * i_lim_ncls) + (c_ox_ncls * i))))))
        return n_mt

    def v_losses(self, current: float, charge: bool, c_ox_cls: float, c_red_cls: float, c_ox_ncls: float,
                 c_red_ncls: float, i_lim_cls: float, i_lim_ncls: float) -> tuple[float, float, float]:
        """
        This is the overpotentials of equation 2 in [1].

        Parameters
        ----------
        current
        charge
        c_ox_cls
        c_red_cls
        c_ox_ncls
        c_red_ncls
        i_lim_cls
        i_lim_ncls

        Returns
        -------

        """

        i_0_cls, i_0_ncls = self.i_exchange_current(c_ox_cls, c_red_cls, c_ox_ncls, c_red_ncls)
        # calculate ohmic, activation, mass transport overpotentials
        n_ohmic = abs(current)*self.resistance
        n_act = self.n_activation(current, i_0_cls, i_0_ncls)
        n_mt = self.n_mass_transport(charge, current, c_ox_cls, c_red_cls, c_ox_ncls, c_red_ncls, i_lim_cls, i_lim_ncls)

        n_loss = n_ohmic + n_act + n_mt

        return n_loss, n_act, n_mt

    def nernst_OCV_full(self, c_ox_cls: float, c_red_cls: float, c_ox_ncls: float, c_red_ncls: float) -> float:
        """
        This is equivalent to equation 3 of [1].

        Parameters
        ----------
        c_ox_cls
        c_red_cls
        c_ox_ncls
        c_red_ncls

        Returns
        -------

        """

        # Raise ValueError if a negative concentration is detected
        if ZeroDModel._negative_concentrations(c_ox_cls, c_red_cls, c_ox_ncls, c_red_ncls):
            raise ValueError('Negative concentration detected')

        # will need n_electrons input
        # CLS is negolyte
        if self.cls_negolyte:
            ocv = (self.init_ocv
                   + (NERNST_CONST * log(c_red_cls / c_ox_cls)) + (NERNST_CONST * log(c_ox_ncls / c_red_ncls)))

        # CLS is posolyte
        else:
            ocv = (self.init_ocv
                   - (NERNST_CONST * log(c_red_cls / c_ox_cls)) - (NERNST_CONST * log(c_ox_ncls / c_red_ncls)))
        return ocv

    @staticmethod
    def _cell_voltage(ocv: float, losses: float, charge: bool) -> float:
        """If charging, add overpotentials to OCV, else subtract them."""
        return ocv + losses if charge else ocv - losses

    def coulomb_counter(self, current: float, c_ox_cls: float, c_red_cls: float, c_ox_ncls: float, c_red_ncls: float,
                        mechanism_list: DegradationMechanism = None,
                        crossover_params: Crossover = None) -> tuple[float, float, float, float, float, float]:

        direction = 1 if self.cls_negolyte else -1
        delta_cls = ((self.time_increment * current) / (F * self.cls_volume)) * direction
        delta_ncls = ((self.time_increment * current) / (F * self.ncls_volume)) * direction

        # update CLS concentrations
        c_ox_cls = c_ox_cls - delta_cls
        c_red_cls = c_red_cls + delta_cls
        # update NCLS concentrations
        c_ox_ncls = c_ox_ncls + delta_ncls
        c_red_ncls = c_red_ncls - delta_ncls

        # for no crossover situation
        delta_ox = 0.0
        delta_red = 0.0

        # Now consider effects of user-input degradations and/or crossover

        # no degradation / no crossover
        if (mechanism_list is None) and (crossover_params is None):
            pass

        # degradation / no crossover
        elif (mechanism_list is not None) and (crossover_params is None):
            # possible CLS degradation
            c_ox_cls, c_red_cls = mechanism_list.degrade(c_ox_cls, c_red_cls, self.time_increment, True)
            # possible NCLS degradation
            c_ox_ncls, c_red_ncls = mechanism_list.degrade(c_ox_ncls, c_red_ncls, self.time_increment, False)

        # no degradation / crossover
        elif (mechanism_list is None) and (crossover_params is not None):
            # possible crossover mechanism
            (c_ox_cls, c_red_cls, c_ox_ncls, c_red_ncls, delta_ox,
             delta_red) = crossover_params.crossover(c_ox_cls, c_red_cls, c_ox_ncls, c_red_ncls, self.time_increment,
                                                     self.cls_volume, self.ncls_volume)

        # degradation AND crossover
        else:
            # possible CLS degradation
            c_ox_cls, c_red_cls = mechanism_list.degrade(c_ox_cls, c_red_cls, self.time_increment, True)
            # possible NCLS degradation
            c_ox_ncls, c_red_ncls = mechanism_list.degrade(c_ox_ncls, c_red_ncls, self.time_increment, False)

            # possible crossover mechanism
            (c_ox_cls, c_red_cls, c_ox_ncls, c_red_ncls, delta_ox,
             delta_red) = crossover_params.crossover(c_ox_cls, c_red_cls, c_ox_ncls, c_red_ncls, self.time_increment,
                                                     self.cls_volume, self.ncls_volume)

        return c_ox_cls, c_red_cls, c_ox_ncls, c_red_ncls, delta_ox, delta_red

    @staticmethod
    def _soc(c_ox_cls: float, c_red_cls: float, c_ox_ncls: float, c_red_ncls: float) -> tuple[float, float]:
        """Calculate state-of-charge in each reservoir"""
        soc_cls = (c_red_cls / (c_ox_cls + c_red_cls)) * 100
        # this could be defined differently i.e., cell vs reservoir definition of SOC
        soc_ncls = (c_red_ncls / (c_ox_ncls + c_red_ncls)) * 100
        return soc_cls, soc_ncls

    # is below proper *args unpacking naming style?
    def cv_current_solver(self, current: float, *data: float) -> float:
        (cell_v, ocv, charge, c_ox_cls, c_red_cls, c_ox_ncls, c_red_ncls, i_lim_cls, i_lim_ncls) = data
        # curr has sign but v_losses makes it always positive
        loss_solve, _, _ = self.v_losses(current, charge, c_ox_cls, c_red_cls, c_ox_ncls, c_red_ncls, i_lim_cls,
                                         i_lim_ncls)
        # returns what solver will try to minimize
        return cell_v - ocv - loss_solve if charge else cell_v - ocv + loss_solve


if __name__ == '__main__':
    print('testing')           
        