/* Read-only accessor compiled against the exact MuPDF 1.25.2 public headers. */
#include "mupdf/fitz.h"
#include <stddef.h>

#if FZ_VERSION_MAJOR != 1 || FZ_VERSION_MINOR != 25 || FZ_VERSION_PATCH != 2 || !FZ_ENABLE_ICC
#error C1025_REQUIRES_EXACT_MUPDF_1_25_2_ICC_HEADERS
#endif

const char *c1025_version(void) { return FZ_VERSION; }

int c1025_current_icc(const fz_colorspace *cs, int type, int flags, int channels,
                     const unsigned char **data, size_t *length)
{
    const fz_buffer *buffer;
    if (!cs || !data || !length) return 0;
    *data = NULL; *length = 0;
    if (cs->type != type || cs->flags != flags || cs->n != channels) return 0;
    if (!(cs->flags & FZ_COLORSPACE_IS_ICC)) return 0;
    if (!((type == FZ_COLORSPACE_GRAY && channels == 1) ||
          (type == FZ_COLORSPACE_RGB && channels == 3) ||
          (type == FZ_COLORSPACE_BGR && channels == 3) ||
          (type == FZ_COLORSPACE_CMYK && channels == 4))) return 0;
    buffer = cs->u.icc.buffer;
    if (!buffer || buffer->refs <= 0 || !buffer->data || buffer->len < 128 ||
        buffer->len > 64U * 1024U * 1024U || buffer->cap < buffer->len) return 0;
    if (buffer->data[36] != 'a' || buffer->data[37] != 'c' ||
        buffer->data[38] != 's' || buffer->data[39] != 'p') return 0;
    *data = buffer->data; *length = buffer->len;
    return 1;
}
