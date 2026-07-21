package com.signaldvr.app.ui.home

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
import com.signaldvr.app.ui.live.LiveTvFragment
import com.signaldvr.app.ui.recordings.RecordingsFragment

data class HomeItem(
    @DrawableRes val iconRes: Int,
    val title: String,
    val subtitle: String,
    val id: Int
)

class HomeFragment : Fragment() {

    private val items = listOf(
        HomeItem(
            R.drawable.ic_home_live_tv,
            "Live TV",
            "Watch live channels with DVR controls",
            1
        ),
        HomeItem(
            R.drawable.ic_home_library,
            "DVR Library",
            "Live, buffered, saved, and recorded TV",
            2
        ),
        HomeItem(
            R.drawable.ic_home_guide,
            "TV Guide",
            "Browse the complete program guide",
            3
        ),
        HomeItem(
            R.drawable.ic_home_recordings,
            "Recordings",
            "Watch your saved recordings",
            4
        )
    )

    override fun onCreateView(
        inflater: LayoutInflater,
        container: ViewGroup?,
        savedInstanceState: Bundle?
    ): View = inflater.inflate(R.layout.fragment_home, container, false)

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        val recycler = view.findViewById<RecyclerView>(R.id.home_grid)

        recycler.layoutManager = GridLayoutManager(requireContext(), 2)
        recycler.adapter = HomeAdapter(items) { item ->
            val fragment = when (item.id) {
                1 -> LiveTvFragment()
                2 -> LibraryFragment()
                3 -> EpgFragment()
                4 -> RecordingsFragment()
                else -> return@HomeAdapter
            }

            parentFragmentManager.beginTransaction()
                .replace(R.id.main_container, fragment)
                .addToBackStack(null)
                .commit()
        }

        recycler.requestFocus()
    }
}

class HomeAdapter(
    private val items: List<HomeItem>,
    private val onClick: (HomeItem) -> Unit
) : RecyclerView.Adapter<HomeAdapter.VH>() {

    inner class VH(view: View) : RecyclerView.ViewHolder(view) {
        val icon: ImageView = view.findViewById(R.id.home_item_icon)
        val title: TextView = view.findViewById(R.id.home_item_title)
        val subtitle: TextView = view.findViewById(R.id.home_item_subtitle)

        init {
            view.isFocusable = true

            view.setOnClickListener {
                val position = bindingAdapterPosition
                if (position != RecyclerView.NO_POSITION) {
                    onClick(items[position])
                }
            }

            view.setOnFocusChangeListener { itemView, hasFocus ->
                itemView.setBackgroundResource(
                    if (hasFocus) R.drawable.bg_channel_focused
                    else R.drawable.bg_channel_normal
                )

                // Keep the focused border fully inside the TV-safe area.
                // Scaling the whole card caused its edges to be clipped.
                itemView.animate().cancel()
                itemView.scaleX = 1f
                itemView.scaleY = 1f

                icon.animate()
                    .scaleX(if (hasFocus) 1.10f else 1f)
                    .scaleY(if (hasFocus) 1.10f else 1f)
                    .alpha(if (hasFocus) 1f else 0.92f)
                    .setDuration(120L)
                    .start()
            }
        }
    }

    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): VH {
        val view = LayoutInflater.from(parent.context)
            .inflate(R.layout.item_home_card, parent, false)
        return VH(view)
    }

    override fun onBindViewHolder(holder: VH, position: Int) {
        val item = items[position]
        holder.icon.setImageResource(item.iconRes)
        holder.icon.contentDescription = item.title
        holder.title.text = item.title
        holder.subtitle.text = item.subtitle
    }

    override fun getItemCount(): Int = items.size
}
