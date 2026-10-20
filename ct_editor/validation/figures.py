"""Standalone PNGs and CSV source data from immutable experiment arrays."""
from io import BytesIO,StringIO
import csv
import json
import os
import tempfile
import numpy as np
os.environ.setdefault('MPLCONFIGDIR',os.path.join(tempfile.gettempdir(),'ct-matplotlib'))
from matplotlib.figure import Figure
from matplotlib.backends.backend_agg import FigureCanvasAgg


def figures(r,job):
    output={};p=r['plan'];n=p['geometry']['resolution'];c=r['configuration']
    caption=(f"{p['protocol']} / {p['comparison']} / {p['purpose']} · {n}×{n} · "
        f"{len(p['geometry']['angles'])} projections · σ={c['sigma']:g} · ω={r['step']['omega']:.5g}\n"
        f"{r['completed_trials']}/{p['trials']} trials · {r['status']} · nominal {1-c['alpha']:.1%}\n"
        'Empirical results; source/bias and randomized probability conditions not established.')
    def save(fig,name,description):
        job.check();fig.suptitle(description,fontsize=14);fig.text(.05,.02,caption,fontsize=8,va='bottom');buf=BytesIO()
        FigureCanvasAgg(fig).print_png(buf);output[name+'.png']=buf.getvalue();fig.clear()
    def csv_values(name,values):
        s=StringIO();w=csv.writer(s);w.writerow(['pixel_index','value']);w.writerows(enumerate(values));output[name+'.csv']=s.getvalue().encode()
    for method,a in r['maps'].items():
        s=r['summaries'][method]
        fields=[('mean','Empirical mean'),('mean_reference','Noiseless mean reference'),('mean_difference','Mean minus reference'),
            ('empirical_variance','Sample variance'),('full_variance','Full-model variance'),('coverage',f"Marginal coverage (N={s['returned_intervals']})"),
            ('mc_se','Monte Carlo SE of coverage'),('mc_lower','Wilson coverage lower'),('mc_upper','Wilson coverage upper'),('mean_width','Mean full interval width'),('actual_bias','Actual signed bias'),('mse','Mean squared error')]
        for page in range(0,len(fields),6):
            fig=Figure(figsize=(12,9));axes=fig.subplots(2,3).ravel();fig.subplots_adjust(bottom=.17,top=.89,hspace=.42,wspace=.6)
            for ax,(key,label) in zip(axes,fields[page:page+6]):
                v=a.get(key);ax.set_title(label,fontsize=10);ax.set_xlabel('Column');ax.set_ylabel('Row')
                if v is None:ax.text(.5,.5,'Unavailable for this conditioning',ha='center',wrap=True,transform=ax.transAxes);ax.set_xticks([]);ax.set_yticks([]);continue
                signed=key in ('mean_difference','actual_bias');low,high=float(v.min()),float(v.max())
                if key in ('mean','mean_reference','empirical_variance','full_variance'):
                    pair=('mean','mean_reference') if key.startswith('mean') else ('empirical_variance','full_variance')
                    available=[a[v] for v in pair if a.get(v) is not None]
                    low=min(float(v.min()) for v in available);high=max(float(v.max()) for v in available)
                if key in ('coverage','mc_lower','mc_upper'):low,high=0,1
                elif signed:high=max(abs(low),abs(high));low=-high
                if low==high:low-=.5;high+=.5
                im=ax.imshow(v.reshape(n,n),cmap='coolwarm' if signed else 'viridis',vmin=low,vmax=high,interpolation='nearest');fig.colorbar(im,ax=ax,fraction=.046,format='%.2g')
                csv_values(method+'_'+key,v)
            save(fig,f'{method}_maps_{page//6}',method+f" · centers {s['numeric_centers']}; intervals {s['returned_intervals']}; no-return {s['terminal']-s['returned_intervals']}")
        fig=Figure(figsize=(11,6));axes=fig.subplots(1,2);fig.subplots_adjust(bottom=.25,top=.85,wspace=.3)
        if a['coverage'] is not None:axes[0].hist(a['coverage'],bins=np.linspace(0,1,21));axes[0].set_xlabel('Marginal coverage estimate');axes[0].set_ylabel('Pixels (correlated)')
        ks=s['selected_k_counts'];axes[1].bar([int(k) for k in ks],list(ks.values()));axes[1].set_xlabel('Selected K');axes[1].set_ylabel('Returned centers')
        save(fig,method+'_distributions',method+' · coverage across pixels and selected iterations')
        for pixel,series in r['series'][method].items():
            if not series:continue
            fig=Figure(figsize=(11,6));axes=fig.subplots(1,2);fig.subplots_adjust(bottom=.25,top=.85,wspace=.3)
            values=[v['center'] for v in series];truth=float(r['inputs']['truth'][int(pixel)])
            axes[0].hist(values,bins=min(20,max(1,len(series)//3)));axes[0].axvline(truth,color='black',label='Truth');axes[0].legend();axes[0].set_xlabel('Reconstructed density');axes[0].set_ylabel('Trials')
            for v in series:
                if v['lower'] is not None:axes[1].plot([v['trial_index']]*2,[v['lower'],v['upper']],color='#28776a' if v['inclusion'] else '#bc4827',alpha=.65)
            axes[1].scatter([v['trial_index'] for v in series],values,s=8);axes[1].axhline(truth,color='black');axes[1].set_xlabel('Original trial index');axes[1].set_ylabel('Raw density interval')
            save(fig,f'{method}_pixel_{pixel}',f'{method} · pixel {pixel} · retained trial outcomes')
    for key,t in r['representatives'].items():
        fig=Figure(figsize=(12,6));axes=fig.subplots(1,3);fig.subplots_adjust(bottom=.26,top=.85,wspace=.32)
        low=min(0,float(t['center'].min()));high=max(1,float(t['center'].max()))
        for ax,v,title in zip(axes,[r['inputs']['truth'],t['center'],t['half_width']],['Frozen truth','Representative reconstruction','Interval half-width']):
            ax.set_title(title);ax.set_xlabel('Column');ax.set_ylabel('Row')
            if v is None:ax.text(.5,.5,'Unavailable',ha='center');continue
            im=ax.imshow(v.reshape(n,n),vmin=low if title!='Interval half-width' else None,vmax=high if title!='Interval half-width' else None,cmap='gray' if title!='Interval half-width' else 'viridis',interpolation='nearest');fig.colorbar(im,ax=ax,fraction=.046,format='%.2g')
        save(fig,'trial_'+key,f"Predeclared representative trial {t['trial_index']} · {t['method']} · K={t['k']}; not empirical mean")
        fig=Figure(figsize=(10,6));axes=fig.subplots(1,2);fig.subplots_adjust(bottom=.26,top=.85,wspace=.3)
        clean=r['inputs']['clean'];y=t['y'];lo=min(clean.min(),y.min());hi=max(clean.max(),y.max());shape=(len(p['geometry']['angles']),len(p['geometry']['offsets']))
        for ax,v,title in zip(axes,[clean,y],['Clean sinogram','Noisy sinogram']):
            im=ax.imshow(v.reshape(shape).T,origin='lower',aspect='auto',vmin=lo,vmax=hi);fig.colorbar(im,ax=ax);ax.set_title(title);ax.set_xlabel('Angle index');ax.set_ylabel('Detector index')
        save(fig,'sinogram_'+key,f"Shared scale · representative trial {t['trial_index']}")
    if r['bias_curve']:
        fig=Figure(figsize=(9,6));ax=fig.subplots();fig.subplots_adjust(bottom=.25,top=.85)
        for key,label in [('actual_squared_bias','Actual squared bias'),('full_covariance_trace','Full covariance trace'),('expected_squared_error','Expected squared error')]:
            points=[v for v in r['bias_curve'] if v[key] is not None]
            if points:ax.plot([v['k'] for v in points],[v[key] for v in points],marker='o',label=label)
        ax.legend();ax.set_xlabel('Predeclared iteration K');ax.set_ylabel('Image-wide squared error / variance');save(fig,'bias_variance','Bias–variance diagnostic; not an oracle stopping rule')
    from .service import plain
    output['plot_source.json']=json.dumps(plain({key:r[key] for key in ('study_id','plan','configuration','step','summaries','maps','representatives','series','bias_curve','inputs')}),allow_nan=False).encode()
    output['captions.txt']=caption.encode()
    return output
