#include <flutter/dart_project.h>
#include <flutter/flutter_view_controller.h>
#include <windows.h>
#include <dwmapi.h> // Required for Rounded Corners & Shadow

#include "flutter_window.h"
#include "utils.h"

#include <bitsdojo_window_windows/bitsdojo_window_plugin.h>

// Initialize bitsdojo_window
auto bdw = bitsdojo_window_configure(BDW_CUSTOM_FRAME | BDW_HIDE_ON_STARTUP);

int APIENTRY wWinMain(_In_ HINSTANCE instance, _In_opt_ HINSTANCE prev,
                      _In_ wchar_t *command_line, _In_ int show_command) {
  
  // 1. Console setup for debugging
  if (!::AttachConsole(ATTACH_PARENT_PROCESS) && ::IsDebuggerPresent()) {
    CreateAndAttachConsole();
  }

  // 2. COM Initialization
  ::CoInitializeEx(nullptr, COINIT_APARTMENTTHREADED);

  // 3. Project Setup
  flutter::DartProject project(L"data");

  std::vector<std::string> command_line_arguments =
      GetCommandLineArguments();

  project.set_dart_entrypoint_arguments(std::move(command_line_arguments));

  // 4. Create Window
  FlutterWindow window(project);
  Win32Window::Point origin(10, 10);
  Win32Window::Size size(1280, 720);
  
  if (!window.Create(L"LocalCUA", origin, size)) {
    return EXIT_FAILURE;
  }

  // --- FEATURE: ROUNDED CORNERS ---
  // Force Windows 11 rounded corners preference
  DWM_WINDOW_CORNER_PREFERENCE preference = DWMWCP_ROUND;
  DwmSetWindowAttribute(
      window.GetHandle(), 
      DWMWA_WINDOW_CORNER_PREFERENCE, 
      &preference, 
      sizeof(preference)
  );

  // --- FEATURE: DROP SHADOW ---
  // Extend the frame by 1 pixel (invisible) to trigger the native window shadow
  MARGINS margins = {0, 0, 0, 1};
  DwmExtendFrameIntoClientArea(window.GetHandle(), &margins);

  // 5. Lifecycle setup
  window.SetQuitOnClose(true);

  // 6. Message Loop
  ::MSG msg;
  while (::GetMessage(&msg, nullptr, 0, 0)) {
    ::TranslateMessage(&msg);
    ::DispatchMessage(&msg);
  }

  ::CoUninitialize();
  return EXIT_SUCCESS;
}