#include <math.h>
#include <string.h>
#include "temporal_camera.h"

void temporal_multiply(const float a[16], const float b[16], float out[16]) {
    float r[16]={0};
    for(int y=0;y<4;y++)for(int x=0;x<4;x++)for(int k=0;k<4;k++)r[y*4+x]+=a[y*4+k]*b[k*4+x];
    memcpy(out,r,sizeof r);
}
int temporal_camera_complete(float rows[64], float aspect) {
    for(int i=0;i<12;i++)if(!isfinite(rows[i]))return 0;
    if(!isfinite(aspect)||aspect<0.5f||aspect>4||!isfinite(rows[33])||fabsf(rows[33])<0.01f||
       rows[56]<=0||rows[57]<=rows[56]||!isfinite(rows[57]))return 0;
    if(isfinite(rows[28]) && rows[62]>0)return 1;
    // The captured world block can lack its horizontal projection even though its view, vertical
    // projection and clipping planes are valid. Reconstruct the centered perspective from those
    // measured values and the game's output aspect, rather than sending NaNs to temporal effects.
    float p[16]={0},v[16]={0};
    memcpy(v,rows,12*sizeof(float));v[15]=1;
    float n=rows[56],f=rows[57];
    p[0]=rows[33]/aspect;p[5]=rows[33];p[10]=-(f+n)/(f-n);p[11]=-2*f*n/(f-n);p[14]=-1;
    memcpy(rows+28,p,sizeof p);temporal_multiply(p,v,rows+12);
    rows[62]=aspect;rows[63]=1/aspect;
    return 1;
}
int temporal_inverse(const float in[16], float out[16]) {
    double a[4][8];
    for(int y=0;y<4;y++)for(int x=0;x<8;x++)a[y][x]=x<4?in[y*4+x]:(x-4==y);
    for(int k=0;k<4;k++) {
        int p=k;for(int y=k+1;y<4;y++)if(fabs(a[y][k])>fabs(a[p][k]))p=y;
        if(fabs(a[p][k])<1e-12)return 0;
        for(int x=0;x<8;x++){double t=a[k][x];a[k][x]=a[p][x];a[p][x]=t;}
        double d=a[k][k];for(int x=0;x<8;x++)a[k][x]/=d;
        for(int y=0;y<4;y++)if(y!=k){d=a[y][k];for(int x=0;x<8;x++)a[y][x]-=d*a[k][x];}
    }
    for(int y=0;y<4;y++)for(int x=0;x<4;x++)out[y*4+x]=(float)a[y][x+4];
    return 1;
}
int temporal_camera(const float rows[64], TemporalCamera *c) {
    memset(c,0,sizeof *c);
    for(int i=0;i<64;i++)if(!isfinite(rows[i]))return 0;
    memcpy(c->view,rows,12*sizeof(float));c->view[15]=1;
    if(!temporal_inverse(c->view,c->inverse_view))return 0;
    memcpy(c->view_projection,rows+12,16*sizeof(float));
    float p[16];memcpy(p,rows+28,sizeof p);
    if(fabsf(p[14])<0.1f || fabsf(p[5])<0.01f || fabsf(p[0])<0.01f)return 0;
    // OpenGL clip depth is [-w,w], while both APIs consume the stored Vulkan [0,1] depth.
    c->near_plane=rows[56];c->far_plane=rows[57];
    c->fov=2*atanf(1/fabsf(p[5]));c->aspect=fabsf(p[5]/p[0]);
    if(!isfinite(c->far_plane)||c->near_plane<=0||c->far_plane<=c->near_plane||c->fov<=0||c->fov>=3.14f)return 0;
    for(int x=0;x<4;x++)p[8+x]=(p[8+x]+p[12+x])*0.5f;
    memcpy(c->projection,p,sizeof p);
    temporal_multiply(p,c->view,c->view_projection);
    return temporal_inverse(p,c->inverse_projection)&&temporal_inverse(c->view_projection,c->inverse_view_projection);
}
