// vk_present - minimal Vulkan xcb client for GL-PRESENT-01 (mainline #3 step 1).
// Every frame is one vkCmdClearColorImage (colour alternates per swapchain image), so the GPU
// does almost nothing and what is left is the cost of handing the frame to the X server.
//   vk_present --size WxH --seconds S [--mode immediate|mailbox|fifo] [--offscreen]
// --offscreen renders the same clear into a device-local image and waits for it every frame,
// with no window, surface or swapchain: the "nothing is presented" control.
// Last stdout line: VK_PRESENT key=value ... ; exit 0 only when at least one frame completed.
//   cc -O2 -Wall -I<mesa>/include -o vk_present vk_present.c -l:libvulkan.so.1 -lxcb
#define VK_USE_PLATFORM_XCB_KHR
#include <vulkan/vulkan.h>
#include <xcb/xcb.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#define CHECK(x) do { VkResult r_ = (x); if (r_ != VK_SUCCESS) { \
    printf("VK_PRESENT_FAIL call=%s result=%d line=%d\n", #x, r_, __LINE__); exit(3); } } while (0)
#define MAXIMG 8

static double now(void) { struct timespec t; clock_gettime(CLOCK_MONOTONIC, &t); return t.tv_sec + t.tv_nsec / 1e9; }

static const char *mode_name(VkPresentModeKHR m) {
    switch (m) {
    case VK_PRESENT_MODE_IMMEDIATE_KHR: return "immediate";
    case VK_PRESENT_MODE_MAILBOX_KHR: return "mailbox";
    case VK_PRESENT_MODE_FIFO_KHR: return "fifo";
    case VK_PRESENT_MODE_FIFO_RELAXED_KHR: return "fifo_relaxed";
    default: return "other";
    }
}

static uint32_t find_mem(VkPhysicalDevice pd, uint32_t bits, VkMemoryPropertyFlags want) {
    VkPhysicalDeviceMemoryProperties mp;
    vkGetPhysicalDeviceMemoryProperties(pd, &mp);
    for (uint32_t i = 0; i < mp.memoryTypeCount; i++)
        if ((bits & (1u << i)) && (mp.memoryTypes[i].propertyFlags & want) == want)
            return i;
    printf("VK_PRESENT_FAIL call=find_mem\n");
    exit(3);
}

static void clear_cmds(VkCommandBuffer cb, VkImage img, VkImageLayout final_layout, float shade) {
    VkCommandBufferBeginInfo bi = { .sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO };
    VkImageSubresourceRange rng = { VK_IMAGE_ASPECT_COLOR_BIT, 0, 1, 0, 1 };
    VkImageMemoryBarrier b = { .sType = VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER, .srcAccessMask = 0,
        .dstAccessMask = VK_ACCESS_TRANSFER_WRITE_BIT, .oldLayout = VK_IMAGE_LAYOUT_UNDEFINED,
        .newLayout = VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL, .srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
        .dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED, .image = img, .subresourceRange = rng };
    VkClearColorValue col = { .float32 = { shade, 0.5f, 1.0f - shade, 1.0f } };
    CHECK(vkBeginCommandBuffer(cb, &bi));
    vkCmdPipelineBarrier(cb, VK_PIPELINE_STAGE_TOP_OF_PIPE_BIT, VK_PIPELINE_STAGE_TRANSFER_BIT, 0, 0, NULL, 0, NULL, 1, &b);
    vkCmdClearColorImage(cb, img, VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL, &col, 1, &rng);
    b.srcAccessMask = VK_ACCESS_TRANSFER_WRITE_BIT;
    b.dstAccessMask = 0;
    b.oldLayout = VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL;
    b.newLayout = final_layout;
    vkCmdPipelineBarrier(cb, VK_PIPELINE_STAGE_TRANSFER_BIT, VK_PIPELINE_STAGE_BOTTOM_OF_PIPE_BIT, 0, 0, NULL, 0, NULL, 1, &b);
    CHECK(vkEndCommandBuffer(cb));
}

int main(int argc, char **argv) {
    uint32_t W = 600, H = 600;
    double secs = 20;
    int offscreen = 0;
    const char *want_mode = "immediate";
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--size") && i + 1 < argc) sscanf(argv[++i], "%ux%u", &W, &H);
        else if (!strcmp(argv[i], "--seconds") && i + 1 < argc) secs = atof(argv[++i]);
        else if (!strcmp(argv[i], "--mode") && i + 1 < argc) want_mode = argv[++i];
        else if (!strcmp(argv[i], "--offscreen")) offscreen = 1;
        else { printf("VK_PRESENT_FAIL bad_arg=%s\n", argv[i]); return 2; }
    }
    setvbuf(stdout, NULL, _IOLBF, 0);

    const char *exts[] = { VK_KHR_SURFACE_EXTENSION_NAME, VK_KHR_XCB_SURFACE_EXTENSION_NAME };
    VkApplicationInfo app = { .sType = VK_STRUCTURE_TYPE_APPLICATION_INFO, .pApplicationName = "vk_present",
                              .apiVersion = VK_API_VERSION_1_1 };
    VkInstanceCreateInfo ici = { .sType = VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO, .pApplicationInfo = &app,
                                 .enabledExtensionCount = offscreen ? 0 : 2, .ppEnabledExtensionNames = exts };
    VkInstance inst;
    CHECK(vkCreateInstance(&ici, NULL, &inst));
    uint32_t npd = 1;
    VkPhysicalDevice pd;
    VkResult er = vkEnumeratePhysicalDevices(inst, &npd, &pd);
    if ((er != VK_SUCCESS && er != VK_INCOMPLETE) || npd == 0) { printf("VK_PRESENT_FAIL call=no_device\n"); return 3; }
    VkPhysicalDeviceDriverProperties drv = { .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_DRIVER_PROPERTIES };
    VkPhysicalDeviceProperties2 p2 = { .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PROPERTIES_2, .pNext = &drv };
    vkGetPhysicalDeviceProperties2(pd, &p2);
    printf("VK_DEVICE name=\"%s\" driver=\"%s\" info=\"%s\"\n", p2.properties.deviceName, drv.driverName, drv.driverInfo);

    uint32_t nqf = 0;
    vkGetPhysicalDeviceQueueFamilyProperties(pd, &nqf, NULL);
    uint32_t qf = 0;   // family 0 has graphics on Turnip and lavapipe; presentation support is checked below
    float prio = 1;
    VkDeviceQueueCreateInfo qci = { .sType = VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO, .queueFamilyIndex = qf,
                                    .queueCount = 1, .pQueuePriorities = &prio };
    const char *dexts[] = { VK_KHR_SWAPCHAIN_EXTENSION_NAME };
    VkDeviceCreateInfo dci = { .sType = VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO, .queueCreateInfoCount = 1,
                               .pQueueCreateInfos = &qci, .enabledExtensionCount = offscreen ? 0 : 1,
                               .ppEnabledExtensionNames = dexts };
    VkDevice dev;
    CHECK(vkCreateDevice(pd, &dci, NULL, &dev));
    VkQueue q;
    vkGetDeviceQueue(dev, qf, 0, &q);
    VkCommandPoolCreateInfo cpi = { .sType = VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO, .queueFamilyIndex = qf };
    VkCommandPool pool;
    CHECK(vkCreateCommandPool(dev, &cpi, NULL, &pool));

    VkImage imgs[MAXIMG];
    VkCommandBuffer cbs[MAXIMG];
    uint32_t nimg = 0;
    VkSwapchainKHR sc = VK_NULL_HANDLE;
    VkPresentModeKHR mode = VK_PRESENT_MODE_FIFO_KHR;
    VkFormat fmt = VK_FORMAT_B8G8R8A8_UNORM;
    xcb_connection_t *conn = NULL;

    if (offscreen) {
        VkImageCreateInfo ii = { .sType = VK_STRUCTURE_TYPE_IMAGE_CREATE_INFO, .imageType = VK_IMAGE_TYPE_2D,
            .format = fmt, .extent = { W, H, 1 }, .mipLevels = 1, .arrayLayers = 1, .samples = VK_SAMPLE_COUNT_1_BIT,
            .tiling = VK_IMAGE_TILING_OPTIMAL, .usage = VK_IMAGE_USAGE_TRANSFER_DST_BIT,
            .initialLayout = VK_IMAGE_LAYOUT_UNDEFINED };
        nimg = 2;
        for (uint32_t i = 0; i < nimg; i++) {
            VkMemoryRequirements mr;
            VkDeviceMemory mem;
            CHECK(vkCreateImage(dev, &ii, NULL, &imgs[i]));
            vkGetImageMemoryRequirements(dev, imgs[i], &mr);
            VkMemoryAllocateInfo ai = { .sType = VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO, .allocationSize = mr.size,
                .memoryTypeIndex = find_mem(pd, mr.memoryTypeBits, VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT) };
            CHECK(vkAllocateMemory(dev, &ai, NULL, &mem));
            CHECK(vkBindImageMemory(dev, imgs[i], mem, 0));
        }
    } else {
        conn = xcb_connect(NULL, NULL);
        if (xcb_connection_has_error(conn)) { printf("VK_PRESENT_FAIL call=xcb_connect\n"); return 3; }
        xcb_screen_t *scr = xcb_setup_roots_iterator(xcb_get_setup(conn)).data;
        xcb_window_t win = xcb_generate_id(conn);
        uint32_t ev = XCB_EVENT_MASK_EXPOSURE | XCB_EVENT_MASK_STRUCTURE_NOTIFY;
        xcb_create_window(conn, XCB_COPY_FROM_PARENT, win, scr->root, 0, 0, W, H, 0,
                          XCB_WINDOW_CLASS_INPUT_OUTPUT, scr->root_visual, XCB_CW_EVENT_MASK, &ev);
        xcb_map_window(conn, win);
        xcb_flush(conn);
        VkXcbSurfaceCreateInfoKHR si = { .sType = VK_STRUCTURE_TYPE_XCB_SURFACE_CREATE_INFO_KHR,
                                         .connection = conn, .window = win };
        VkSurfaceKHR surf;
        CHECK(vkCreateXcbSurfaceKHR(inst, &si, NULL, &surf));
        VkBool32 sup = VK_FALSE;
        CHECK(vkGetPhysicalDeviceSurfaceSupportKHR(pd, qf, surf, &sup));
        if (!sup) { printf("VK_PRESENT_FAIL call=surface_not_supported\n"); return 3; }
        VkSurfaceCapabilitiesKHR caps;
        CHECK(vkGetPhysicalDeviceSurfaceCapabilitiesKHR(pd, surf, &caps));
        uint32_t nm = 8;
        VkPresentModeKHR modes[8];
        CHECK(vkGetPhysicalDeviceSurfacePresentModesKHR(pd, surf, &nm, modes));
        int found = 0;
        printf("VK_MODES");
        for (uint32_t i = 0; i < nm; i++) {
            printf(" %s", mode_name(modes[i]));
            if (!strcmp(mode_name(modes[i]), want_mode)) { mode = modes[i]; found = 1; }
        }
        printf("\n");
        if (!found) { printf("VK_PRESENT_FAIL call=mode_unavailable want=%s\n", want_mode); return 3; }
        uint32_t nf = 16;
        VkSurfaceFormatKHR fmts[16];
        CHECK(vkGetPhysicalDeviceSurfaceFormatsKHR(pd, surf, &nf, fmts));
        fmt = fmts[0].format;
        for (uint32_t i = 0; i < nf; i++)
            if (fmts[i].format == VK_FORMAT_B8G8R8A8_UNORM) fmt = fmts[i].format;
        uint32_t want = caps.minImageCount + 1;
        if (caps.maxImageCount && want > caps.maxImageCount) want = caps.maxImageCount;
        if (want > MAXIMG) want = MAXIMG;
        VkSwapchainCreateInfoKHR sci = { .sType = VK_STRUCTURE_TYPE_SWAPCHAIN_CREATE_INFO_KHR, .surface = surf,
            .minImageCount = want, .imageFormat = fmt, .imageColorSpace = VK_COLOR_SPACE_SRGB_NONLINEAR_KHR,
            .imageExtent = { W, H }, .imageArrayLayers = 1,
            .imageUsage = VK_IMAGE_USAGE_TRANSFER_DST_BIT | VK_IMAGE_USAGE_COLOR_ATTACHMENT_BIT,
            .imageSharingMode = VK_SHARING_MODE_EXCLUSIVE, .preTransform = caps.currentTransform,
            .compositeAlpha = VK_COMPOSITE_ALPHA_OPAQUE_BIT_KHR, .presentMode = mode, .clipped = VK_TRUE };
        CHECK(vkCreateSwapchainKHR(dev, &sci, NULL, &sc));
        nimg = MAXIMG;
        CHECK(vkGetSwapchainImagesKHR(dev, sc, &nimg, imgs));
    }

    VkCommandBufferAllocateInfo cai = { .sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO, .commandPool = pool,
                                        .level = VK_COMMAND_BUFFER_LEVEL_PRIMARY, .commandBufferCount = nimg };
    CHECK(vkAllocateCommandBuffers(dev, &cai, cbs));
    for (uint32_t i = 0; i < nimg; i++)
        clear_cmds(cbs[i], imgs[i], offscreen ? VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL : VK_IMAGE_LAYOUT_PRESENT_SRC_KHR,
                   (float) (i % 2));

    enum { SLOTS = 2 };
    VkSemaphore acq[SLOTS], done[MAXIMG];
    VkFence fence[SLOTS];
    VkSemaphoreCreateInfo semi = { .sType = VK_STRUCTURE_TYPE_SEMAPHORE_CREATE_INFO };
    VkFenceCreateInfo fi = { .sType = VK_STRUCTURE_TYPE_FENCE_CREATE_INFO, .flags = VK_FENCE_CREATE_SIGNALED_BIT };
    for (int i = 0; i < SLOTS; i++) { CHECK(vkCreateSemaphore(dev, &semi, NULL, &acq[i])); CHECK(vkCreateFence(dev, &fi, NULL, &fence[i])); }
    for (uint32_t i = 0; i < nimg; i++) CHECK(vkCreateSemaphore(dev, &semi, NULL, &done[i]));

    uint64_t frames = 0, suboptimal = 0;
    double t0 = now(), t;
    VkPipelineStageFlags ws = VK_PIPELINE_STAGE_TRANSFER_BIT;
    while ((t = now()) - t0 < secs) {
        int s = frames % SLOTS;
        CHECK(vkWaitForFences(dev, 1, &fence[s], VK_TRUE, UINT64_MAX));
        CHECK(vkResetFences(dev, 1, &fence[s]));
        uint32_t idx = frames % nimg;
        if (!offscreen) {
            VkResult ar = vkAcquireNextImageKHR(dev, sc, UINT64_MAX, acq[s], VK_NULL_HANDLE, &idx);
            if (ar == VK_SUBOPTIMAL_KHR) suboptimal++;
            else if (ar != VK_SUCCESS) { printf("VK_PRESENT_FAIL call=acquire result=%d frames=%llu\n", ar, (unsigned long long) frames); return 3; }
        }
        VkSubmitInfo sub = { .sType = VK_STRUCTURE_TYPE_SUBMIT_INFO, .waitSemaphoreCount = offscreen ? 0 : 1,
            .pWaitSemaphores = &acq[s], .pWaitDstStageMask = &ws, .commandBufferCount = 1, .pCommandBuffers = &cbs[idx],
            .signalSemaphoreCount = offscreen ? 0 : 1, .pSignalSemaphores = &done[idx] };
        CHECK(vkQueueSubmit(q, 1, &sub, fence[s]));
        if (offscreen) {
            CHECK(vkWaitForFences(dev, 1, &fence[s], VK_TRUE, UINT64_MAX));   // one round trip per frame
        } else {
            VkPresentInfoKHR pi = { .sType = VK_STRUCTURE_TYPE_PRESENT_INFO_KHR, .waitSemaphoreCount = 1,
                .pWaitSemaphores = &done[idx], .swapchainCount = 1, .pSwapchains = &sc, .pImageIndices = &idx };
            VkResult pr = vkQueuePresentKHR(q, &pi);
            if (pr == VK_SUBOPTIMAL_KHR) suboptimal++;
            else if (pr != VK_SUCCESS) { printf("VK_PRESENT_FAIL call=present result=%d frames=%llu\n", pr, (unsigned long long) frames); return 3; }
            xcb_generic_event_t *e;
            while ((e = xcb_poll_for_event(conn))) free(e);
        }
        frames++;
    }
    CHECK(vkDeviceWaitIdle(dev));
    double el = now() - t0;
    printf("VK_PRESENT frames=%llu secs=%.3f fps=%.1f size=%ux%u offscreen=%d mode=%s images=%u format=%d suboptimal=%llu\n",
           (unsigned long long) frames, el, frames / el, W, H, offscreen, offscreen ? "none" : mode_name(mode), nimg,
           (int) fmt, (unsigned long long) suboptimal);
    return frames > 0 ? 0 : 4;
}
