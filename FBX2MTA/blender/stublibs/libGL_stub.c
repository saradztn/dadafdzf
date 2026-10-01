#include <stddef.h>
typedef void* ptr;
static ptr dummy(void){return 0;}
ptr glFinish(void);
ptr glXChooseFBConfig(void);
ptr glXCreateContext(void);
ptr glXCreateNewContext(void);
ptr glXCreateWindow(void);
ptr glXDestroyContext(void);
ptr glXGetCurrentContext(void);
ptr glXGetCurrentDisplay(void);
ptr glXGetCurrentDrawable(void);
ptr glXGetProcAddress(const char*p);
ptr glXGetProcAddressARB(const char*p);
ptr glXGetVisualFromFBConfig(void);
ptr glXMakeContextCurrent(void);
ptr glXMakeCurrent(void);
ptr glXQueryContext(void);
ptr glXSwapBuffers(void);
ptr glFinish(void){return 0;}
ptr glXChooseFBConfig(void){return 0;}
ptr glXCreateContext(void){return 0;}
ptr glXCreateNewContext(void){return 0;}
ptr glXCreateWindow(void){return 0;}
ptr glXDestroyContext(void){return 0;}
ptr glXGetCurrentContext(void){return 0;}
ptr glXGetCurrentDisplay(void){return 0;}
ptr glXGetCurrentDrawable(void){return 0;}
ptr glXGetProcAddress(const char*p){return (ptr)dummy;}
ptr glXGetProcAddressARB(const char*p){return (ptr)dummy;}
ptr glXGetVisualFromFBConfig(void){return 0;}
ptr glXMakeContextCurrent(void){return 0;}
ptr glXMakeCurrent(void){return 0;}
ptr glXQueryContext(void){return 0;}
ptr glXSwapBuffers(void){return 0;}
