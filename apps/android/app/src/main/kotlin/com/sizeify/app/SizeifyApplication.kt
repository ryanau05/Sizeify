package com.sizeify.app

import android.app.Application
import dagger.hilt.android.HiltAndroidApp

/** Application entry point. `@HiltAndroidApp` triggers Hilt code generation. */
@HiltAndroidApp
class SizeifyApplication : Application()
