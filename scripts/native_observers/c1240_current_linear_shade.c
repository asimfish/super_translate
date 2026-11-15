/* Read-only current callback, exact MuPDF 1.25.2 ABI, bounded linear family. */
#include "mupdf/fitz.h"
#include <math.h>
#include <stddef.h>
#if FZ_VERSION_MAJOR != 1 || FZ_VERSION_MINOR != 25 || FZ_VERSION_PATCH != 2 || !FZ_ENABLE_ICC
#error C1240_REQUIRES_EXACT_MUPDF_1_25_2_ICC_HEADERS
#endif

const char *c1240_version(void) { return FZ_VERSION; }

int c1240_current_linear(const fz_shade *shade, int type, int stride,
                        const fz_colorspace *space, float *geometry, int *extend,
                        const float **function, size_t *count)
{
    if (!shade || !space || !geometry || !extend || !function || !count) return 0;
    *function = NULL; *count = 0;
    if (type != FZ_LINEAR || shade->type != type || shade->colorspace != space ||
        shade->function_stride != stride || stride != space->n + 1 || stride < 2 || stride > 5 ||
        shade->use_background || shade->buffer || !shade->function) return 0;
    geometry[0]=shade->bbox.x0; geometry[1]=shade->bbox.y0;
    geometry[2]=shade->bbox.x1; geometry[3]=shade->bbox.y1;
    geometry[4]=shade->matrix.a; geometry[5]=shade->matrix.b;
    geometry[6]=shade->matrix.c; geometry[7]=shade->matrix.d;
    geometry[8]=shade->matrix.e; geometry[9]=shade->matrix.f;
    for (int i=0; i<2; ++i) {
        extend[i]=shade->u.l_or_r.extend[i];
        if (extend[i] != 0 && extend[i] != 1) return 0;
        for (int j=0; j<3; ++j) geometry[10+3*i+j]=shade->u.l_or_r.coords[i][j];
    }
    for (int i=0; i<16; ++i) if (!isfinite(geometry[i])) return 0;
    for (size_t i=0; i<256*(size_t)stride; ++i) if (!isfinite(shade->function[i])) return 0;
    *function=shade->function; *count=256*(size_t)stride;
    return 1;
}
