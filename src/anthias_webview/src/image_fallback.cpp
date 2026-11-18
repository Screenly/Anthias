#include "image_fallback.h"

namespace image_fallback
{

bool shouldUseLastRasterImage(bool currentImageIsNull, bool fallbackAllowed)
{
    return currentImageIsNull && fallbackAllowed;
}

}  // namespace image_fallback
