import numpy as np


def init_diagnostics(pyom):
    """
    initialize diagnostic routines
    """

    print("Diagnostic setup:")

    if pyom.enable_diag_ts_monitor:
        print("time step monitor every {} seconds/{} time steps".format(pyom.ts_monint,pyom.ts_monint/pyom.dt_tracer))

    if pyom.enable_diag_tracer_content:
        print("monitor tracer content every {} seconds/{} time steps".format(pyom.trac_cont_int,pyom.trac_cont_int/pyom.dt_tracer))

    if pyom.enable_diag_snapshots:
        print("writing snapshots every {} seconds/{} time steps".format(pyom.snapint,pyom.snapint/pyom.dt_tracer))
        init_snap_cdf()

    if pyom.enable_diag_averages:
        print("writing time averages every {} seconds/{} time steps".format(pyom.aveint,pyom.aveint/pyom.dt_tracer))
        print(" averaging every {} time step".format(pyom.avefreq/pyom.dt_tracer))

    if pyom.enable_diag_energy:
        print("writing energetics every {} seconds/{} time steps".format(pyom.energint,pyom.energint/dt_tracer))
        print(" diagnosing every {} time step".format(pyom.energfreq/pyom.dt_tracer))
        init_diag_energy()

    if pyom.enable_diag_overturning:
        print("writing isopyc. overturning every {} seconds/{} time steps".format(pyom.overint,pyom.overint/pyom.dt_tracer))
        print(" diagnosing every {} time step".format(pyom.overfreq/pyom.dt_tracer))
        init_diag_overturning()

    if pyom.enable_diag_particles:
        print("writing particles every {} seconds/{} time steps".format(pyom.particles_int,pyom.particles_int/pyom.dt_tracer))
        set_particles()
        init_diag_particles()
        init_write_particles()


def diagnose(pyom):
    """
    call diagnostic routines
    """

    GM_strfct_diagnosed = False
    time = pyom.itt*pyom.dt_tracer

    if pyom.enable_diag_ts_monitor and time % pyom.ts_monint < pyom.dt_tracer:
        print("itt={} time={}s congr.itts={}".format(pyom.itt,time,pyom.congr_itts))
        if not pyom.enable_hydrostatic:
            print("congr. non hydro itts={}".format(congr_itts_non_hydro))
        diag_cfl()

    if pyom.enable_diag_tracer_content and time % pyom.trac_cont_int < pyom.dt_tracer:
        diag_tracer_content()

    if pyom.enable_diag_energy and time % pyom.energfreq < pyom.dt_tracer:
        diagnose_energy()

    if pyom.enable_diag_energy and time % pyom.energint < pyom.dt_tracer:
        write_energy()

    if pyom.enable_diag_averages and time % pyom.avefreq < pyom.dt_tracer:
        if pyom.enable_neutral_diffusion and pyom.enable_skew_diffusion and not GM_strfct_diagnosed:
            isoneutral_diag_streamfunction()
            GM_strfct_diagnosed = True
        diag_averages()

    if pyom.enable_diag_averages and time % pyom.aveint < pyom.dt_tracer:
        write_averages()

    if pyom.enable_diag_snapshots and time % pyom.snapint < pyom.dt_tracer:
        if pyom.enable_neutral_diffusion and pyom.enable_skew_diffusion and not pyom.GM_strfct_diagnosed:
            isoneutral_diag_streamfunction()
            GM_strfct_diagnosed = True
        diag_snap()

    if pyom.enable_diag_overturning and time % pyom.overfreq < pyom.dt_tracer:
        if pyom.enable_neutral_diffusion and pyom.enable_skew_diffusion and not GM_strfct_diagnosed:
            isoneutral_diag_streamfunction()
            GM_strfct_diagnosed = True
        diag_overturning()

    if pyom.enable_diag_overturning and time % pyom.overint < pyom.dt_tracer:
        write_overturning()

    if pyom.enable_diag_particles:
        integrate_particles()
        if time % pyom.particles_int < pyom.dt_tracer:
            write_particles()


def diag_cfl(pyom):
    """
    check for CFL violation
    """
    cfl = 0
    wcfl = 0.
    for k in xrange(1,nz): # k=1,nz
        for j in xrange(js_pe,je_pe): # j=js_pe,je_pe
            for i in xrange(is_pe,ie_pe): # i=is_pe,ie_pe
                cfl = max(cfl, abs(u[i,j,k,tau])*maskU[i,j,k]/(cost[j]*dxt[i])*dt_tracer)
                cfl = max(cfl, abs(v[i,j,k,tau])*maskV[i,j,k]/dyt[j]*dt_tracer)
                wcfl = max(wcfl, abs(w[i,j,k,tau])*maskW[i,j,k]/dzt[k]*dt_tracer)

    # global_max(cfl); call global_max(wcfl)
    cfl = np.max(cfl)
    wcfl = np.max(wcfl)

    # !if (cfl > 0.5.or.wcfl > 0.5) then
    # !  if (my_pe==0) print'(/a,f12.6)','ERROR:  maximal CFL number = ',max(cfl,wcfl)
    # !  if (my_pe==0) print'(a,i9,a/)' ,' at itt = ',itt,' ... stopping integration '
    # !  if (.not. enable_diag_snapshots )   call init_snap_cdf
    # !  call diag_snap
    # !  call halt_stop(' in diag_cfl')
    # !endif
    # ! check for NaN

    if np.isnan(cfl) or np.isnan(wcfl):
        print("ERROR: CFL number is NaN att itt = {} ... stopping integration".format(itt))
        if not pyom.enable_diag_snapshots:
            init_snap_cdf()
        diag_snap()
        halt_stop(' in diag_cfl')

    print("maximal hor. CFL number = {}".format(cfl))
    print("maximal ver. CFL number = {}".format(wcfl))

    if pyom.enable_eke or pyom.enable_tke or pyom.enable_idemix:
        cfl = 0.0
        wcfl = 0.0
        for k in xrange(1,nz): # k=1,nz
            for j in xrange(js_pe,je_pe): # j=js_pe,je_pe
                for i in xrange(is_pe,ie_pe): # i=is_pe,ie_pe
                    cfl = max(cfl, abs(u_wgrid[i,j,k])*maskU[i,j,k]/(cost[j]*dxt[i])*dt_tracer)
                    cfl = max(cfl, abs(v_wgrid[i,j,k])*maskV[i,j,k]/dyt[j]*dt_tracer)
                    wcfl = max(wcfl, abs(w_wgrid[i,j,k])*maskW[i,j,k]/dzt[k]*dt_tracer)
        cfl = np.max(cfl)
        wcfl = np.max(wcfl)
        print("maximal hor. CFL number on w grid = {}".format(cfl))
        print("maximal ver. CFL number on w grid = {}".format(wcfl))


_tempm1 = 0.
_saltm1 = 0.
_vtemp1 = 0.
_vsalt1 = 0.

def diag_tracer_content(pyom):
    """
    Diagnose tracer content
    """
    # real*8, save :: tempm1=0.,saltm1=0.,vtemp1=0.,vsalt1=0.

    volm = 0
    tempm = 0
    vtemp = 0
    saltm = 0
    vsalt = 0

    for k in xrange(1,nz): # k=1,nz
        for j in xrange(js_pe,je_pe): # j=js_pe,je_pe
            for i in xrange(is_pe,ie_pe): # i=is_pe,ie_pe
                fxa = area_t[i,j]*dzt[k]*maskT[i,j,k]
                volm = volm + fxa
                tempm = tempm + fxa*temp[i,j,k,tau]
                saltm = saltm + fxa*salt[i,j,k,tau]
                vtemp = vtemp + temp[i,j,k,tau]**2*fxa
                vsalt = vsalt + salt[i,j,k,tau]**2*fxa

    print("")
    print("mean temperature {} change to last {}".format(tempm/volm,(tempm-_tempm1)/volm))
    print("mean salinity    {} change to last {}".format(saltm/volm,(saltm-_saltm1)/volm))
    print("temperature var. {} change to last {}".format(vtemp/volm,(vtemp-_vtemp1)/volm))
    print("salinity var.    {} change to last {}".format(vsalt/volm,(vsalt-_vsalt1)/volm))

    global _tempm1, _vtemp1, _saltm1, _vsalt1
    _tempm1 = tempm
    _vtemp1 = vtemp
    _saltm1 = saltm
    _vsalt1 = vsalt