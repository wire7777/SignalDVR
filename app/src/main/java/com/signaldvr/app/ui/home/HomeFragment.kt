package com.signaldvr.app.ui.home

import android.graphics.Color
import android.graphics.drawable.GradientDrawable
import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.ImageView
import android.widget.TextView
import androidx.annotation.DrawableRes
import androidx.fragment.app.Fragment
import androidx.recyclerview.widget.GridLayoutManager
import androidx.recyclerview.widget.RecyclerView
import com.signaldvr.app.R
import com.signaldvr.app.ui.guide.EpgFragment
import com.signaldvr.app.ui.library.LibraryFragment
import com.signaldvr.app.ui.manager.DvrManagerFragment
import com.signaldvr.app.ui.recordings.RecordingsFragment
import com.signaldvr.app.ui.settings.SettingsFragment

data class HomeItem(
    @DrawableRes val iconRes: Int,
    val title: String,
    val subtitle: String,
    val id: Int,
)

class HomeFragment : Fragment() {

    private val items = listOf(
        HomeItem(
            iconRes = R.drawable.ic_home_epg,
            title = "EPG",
            subtitle = "Browse the electronic program guide",
            id = 3,
        ),
        HomeItem(
            iconRes = R.drawable.ic_home_library,
            title = "DVR Library",
            subtitle = "Live, buffered, saved, and recorded TV",
            id = 2,
        ),
        HomeItem(
            iconRes = R.drawable.ic_home_recordings,
            title = "Recordings",
            subtitle = "Watch your saved recordings",
            id = 4,
        ),
        HomeItem(
            iconRes = R.drawable.ic_home_dvr_manager,
            title = "DVR Manager",
            subtitle = "Manage series rules and recording tasks",
            id = 6,
        ),
        HomeItem(
            iconRes = R.drawable.ic_home_settings,
            title = "Settings",
            subtitle = "Configure the SignalDVR server",
            id = 5,
        ),
    )

    override fun onCreateView(
        inflater: LayoutInflater,
        container: ViewGroup?,
        savedInstanceState: Bundle?,
    ): View = inflater.inflate(
        R.layout.fragment_home,
        container,
        false,
    )

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        super.onViewCreated(view, savedInstanceState)

        val recycler = view.findViewById<RecyclerView>(R.id.home_grid)
        recycler.layoutManager = GridLayoutManager(requireContext(), 2)
        recycler.adapter = HomeAdapter(items) { item ->
            val destination = when (item.id) {
                2 -> LibraryFragment()
                3 -> EpgFragment()
                4 -> RecordingsFragment()
                5 -> SettingsFragment()
                6 -> DvrManagerFragment()
                else -> return@HomeAdapter
            }

            parentFragmentManager.beginTransaction()
                .replace(R.id.main_container, destination)
                .addToBackStack(null)
                .commit()
        }

        recycler.post {
            recycler.requestFocus()
            recycler.findViewHolderForAdapterPosition(0)
                ?.itemView
                ?.requestFocus()
        }
    }
}

private class HomeAdapter(
    private val items: List<HomeItem>,
    private val onClick: (HomeItem) -> Unit,
) : RecyclerView.Adapter<HomeAdapter.VH>() {

    inner class VH(view: View) : RecyclerView.ViewHolder(view) {
        private val icon: ImageView = view.findViewById(R.id.home_item_icon)
        private val title: TextView = view.findViewById(R.id.home_item_title)
        private val subtitle: TextView = view.findViewById(R.id.home_item_subtitle)

        init {
            view.isFocusable = true
            view.isFocusableInTouchMode = true
            view.background = background(false)

            view.setOnClickListener {
                val position = bindingAdapterPosition
                if (position != RecyclerView.NO_POSITION) {
                    onClick(items[position])
                }
            }

            view.setOnFocusChangeListener { itemView, hasFocus ->
                itemView.animate().cancel()
                itemView.scaleX = 1f
                itemView.scaleY = 1f
                itemView.translationZ = 0f
                itemView.background = background(hasFocus)

                icon.animate()
                    .scaleX(if (hasFocus) 1.08f else 1f)
                    .scaleY(if (hasFocus) 1.08f else 1f)
                    .alpha(if (hasFocus) 1f else 0.92f)
                    .setDuration(120L)
                    .start()
            }
        }

        fun bind(item: HomeItem) {
            icon.setImageResource(item.iconRes)
            icon.contentDescription = item.title
            title.text = item.title
            subtitle.text = item.subtitle
        }

        private fun background(focused: Boolean): GradientDrawable {
            return GradientDrawable().apply {
                shape = GradientDrawable.RECTANGLE
                cornerRadius = 0f
                setColor(
                    Color.parseColor(
                        if (focused) "#143C66" else "#111833"
                    )
                )
                setStroke(
                    (2f * itemView.resources.displayMetrics.density)
                        .toInt()
                        .coerceAtLeast(1),
                    Color.parseColor(
                        if (focused) "#168CFF" else "#2A355C"
                    )
                )
            }
        }
    }

    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): VH {
        val view = LayoutInflater.from(parent.context)
            .inflate(R.layout.item_home_card, parent, false)
        return VH(view)
    }

    override fun onBindViewHolder(holder: VH, position: Int) {
        holder.bind(items[position])
    }

    override fun getItemCount(): Int = items.size
}