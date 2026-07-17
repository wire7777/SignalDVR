package com.signaldvr.app

import android.os.Bundle
import androidx.appcompat.app.AppCompatActivity
import com.signaldvr.app.ui.home.HomeFragment

class MainActivity : AppCompatActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        if (savedInstanceState == null) {
            supportFragmentManager.beginTransaction()
                .replace(R.id.main_container, HomeFragment())
                .commit()
        }
    }
}
