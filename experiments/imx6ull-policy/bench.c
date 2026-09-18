#define _POSIX_C_SOURCE 200809L
#ifdef __APPLE__
#define _DARWIN_C_SOURCE
#endif
#include "policy.h"
#include <errno.h>
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/resource.h>
#include <time.h>

static double now(void) {
    struct timespec t; clock_gettime(CLOCK_MONOTONIC,&t);
    return t.tv_sec + t.tv_nsec * 1e-9;
}
static void sleep_until(double deadline) {
    double delta;
    while ((delta=deadline-now()) > 0) {
        struct timespec t = {(time_t)delta, (long)((delta-floor(delta))*1e9)};
        if (nanosleep(&t,NULL)==0 || errno!=EINTR) break;
    }
}
static int compare(const void *a,const void *b) {
    double x=*(const double *)a,y=*(const double *)b;
    return (x>y)-(x<y);
}
static double pct(double *x,int n,double p) { return x[(int)ceil(n*p)-1]; }
static long frequency_khz(void) {
    FILE *f=fopen("/sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq","r");
    long khz=0;
    if(f) { if(fscanf(f,"%ld",&khz)!=1)khz=0; fclose(f); }
    return khz;
}

int main(int argc,char **argv) {
    if (argc<3 || argc>5) {
        fprintf(stderr,"usage: %s weights.bin fixtures.bin [iterations=5000] [period_ms=0]\n",argv[0]);return 2;
    }
    int n=argc>3?atoi(argv[3]):5000;
    double period=argc>4?atof(argv[4])/1000:0;
    if(n<1 || n>1000000 || !isfinite(period) || period<0 || period>1) return 2;
    DuckPolicy *p=malloc(sizeof(*p));
    if (!p || duck_policy_load(p,argv[1])) {fprintf(stderr,"cannot load weights\n");return 2;}
    FILE *f=fopen(argv[2],"rb");
    if(!f)return 2;
    float row[75],out[14]; unsigned cases=0,failures=0;double max_abs=0;
    size_t got;
    while((got=fread(row,sizeof(float),75,f))==75) {
        if(duck_policy_infer(p,row,out))return 3;
        ++cases;
        for(int j=0;j<14;++j) {
            double e=fabs((double)out[j]-row[61+j]);
            if(e>max_abs)max_abs=e;
            if(!isfinite(row[61+j]) || e>1e-4+1e-4*fabs(row[61+j]))++failures;
        }
    }
    int invalid_fixture=got!=0 || ferror(f);fclose(f);
    if(invalid_fixture || !cases)return 3;
    float obs[61]={0};obs[5]=-1;
    obs[0]=NAN;int rejects_nan=duck_policy_infer(p,obs,out)!=0;obs[0]=INFINITY;
    int rejects_inf=duck_policy_infer(p,obs,out)!=0;obs[0]=0;
    printf("{\"validation_cases\":%u,\"action_values\":%u,\"failed_values\":%u,\"max_abs_error\":%.9g,\"rejects_nan\":%s,\"rejects_inf\":%s}\n",
        cases,cases*14,failures,max_abs,rejects_nan?"true":"false",rejects_inf?"true":"false");
    fflush(stdout);
    if(failures || !rejects_nan || !rejects_inf)return 3;
    for(int i=0;i<200;++i)if(duck_policy_infer(p,obs,out))return 3;
    double *latency=calloc(n,sizeof(double)),*lateness=calloc(n,sizeof(double));
    if(!latency || !lateness)return 2;
    double start=now(),sum=0;int over5=0,over20=0,missed=0;double checksum=0;
    long freq_min=0,freq_max=0;
    struct timespec cpu_start,cpu_end;clock_gettime(CLOCK_PROCESS_CPUTIME_ID,&cpu_start);
    for(int i=0;i<n;++i) {
        double due=start+i*period;
        if(period>0)sleep_until(due);
        obs[48]=0.15f*sinf(i*.013f);obs[50]=.2f*cosf(i*.009f);
        double t=now();if(duck_policy_infer(p,obs,out))return 3;double end=now();
        latency[i]=(end-t)*1000;sum+=latency[i];
        lateness[i]=period>0?fmax(0,(t-due)*1000):0;
        over5+=latency[i]>5;over20+=latency[i]>20;
        if(period>0 && end>due+period)++missed;
        memcpy(obs+34,out,14*sizeof(float));checksum+=out[i%14];
        if(i%50==0) {
            long khz=frequency_khz();
            if(khz>0 && (!freq_min || khz<freq_min))freq_min=khz;
            if(khz>freq_max)freq_max=khz;
        }
    }
    clock_gettime(CLOCK_PROCESS_CPUTIME_ID,&cpu_end);
    double wall=now()-start,cpu=(cpu_end.tv_sec-cpu_start.tv_sec)+(cpu_end.tv_nsec-cpu_start.tv_nsec)*1e-9;
    qsort(latency,n,sizeof(double),compare);qsort(lateness,n,sizeof(double),compare);
    struct rusage usage;getrusage(RUSAGE_SELF,&usage);
    printf("{\"iterations\":%d,\"period_ms\":%.3f,\"wall_s\":%.4f,\"process_cpu_percent\":%.2f,\"latency_ms\":{\"mean\":%.6f,\"p50\":%.6f,\"p95\":%.6f,\"p99\":%.6f,\"max\":%.6f},\"start_lateness_ms\":{\"p99\":%.6f,\"max\":%.6f},\"over_5_ms\":%d,\"over_20_ms\":%d,\"deadline_misses\":%d,\"sampled_cpu_khz\":{\"min\":%ld,\"max\":%ld},\"maxrss_native_units\":%ld,\"checksum\":%.9g}\n",
        n,period*1000,wall,100*cpu/wall,sum/n,pct(latency,n,.5),pct(latency,n,.95),pct(latency,n,.99),latency[n-1],pct(lateness,n,.99),lateness[n-1],over5,over20,missed,freq_min,freq_max,usage.ru_maxrss,checksum);
    free(latency);free(lateness);free(p);return 0;
}
